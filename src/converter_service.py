"""docs2md FastAPI service.

Provides HTTP endpoints that convert documents to Markdown through pluggable
converter engines. Core routing lives here; conversion logic lives in
src/engines/.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from engines import (  # noqa: E402
    MINERU_TIERS,
    ConvertOptions,
    ConvertResult,
    ConvertStatusResponse,
    OutputWriteError,
    all_supported_extensions,
    get_engine,
    list_engines,
    normalize_mineru_pages,
    normalize_mineru_tier,
)
from model_manager import (  # noqa: E402
    models_look_complete,
    write_mineru_config,
)
from resource_control import (  # noqa: E402
    ConversionTimeoutError,
    ResourceBusyError,
    ResourceConfig,
    ResourceController,
    WorkerExecutionError,
    WorkerHTTPError,
)
from resource_limits import (  # noqa: E402
    ResourceLimits,
    UploadBodyLimitMiddleware,
    copy_upload_bounded,
    validate_local_file,
)
from batch_inputs import BatchInputPlan, plan_folder_inputs  # noqa: E402
from batch_jobs import BatchJobManager  # noqa: E402

# "auto" routes by extension; a concrete name forces that engine.
DEFAULT_ENGINE = os.environ.get("DOCS2MD_ENGINE", "auto")
# Engine used when the file type is not claimed by MinerU.
FALLBACK_ENGINE = os.environ.get("DOCS2MD_FALLBACK_ENGINE", "markitdown")
# Uploaded files have no natural parent directory on the server, so they land
# here unless the caller supplies an output_dir.
DEFAULT_UPLOAD_OUTPUT_DIR = Path(
    os.environ.get("DOCS2MD_UPLOAD_OUTPUT_DIR") or PROJECT_ROOT / "output"
)
RESOURCE_CONFIG = ResourceConfig.from_env()
RESOURCE_LIMITS = ResourceLimits.from_env()
RESOURCE_CONTROLLER = ResourceController(RESOURCE_CONFIG)


class _MinerUFields(BaseModel):
    """Shared MinerU 4.x tuning fields and validators for request models."""

    tier: str = Field(
        "standard",
        description="MinerU quality tier: flash, basic, standard, advanced",
    )
    remote: bool = Field(False, description="Use remote MinerU inference (--remote)")
    pages: str = Field(
        "all",
        description="Page spec: 'all', '1-5,8', 'r3-r1' (1-based; rN from end)",
    )

    @field_validator("tier")
    @classmethod
    def _validate_tier(cls, v: str) -> str:
        return normalize_mineru_tier(v)

    @field_validator("pages")
    @classmethod
    def _validate_pages(cls, v: str) -> str:
        return normalize_mineru_pages(v)


class ConvertPathRequest(_MinerUFields):
    """JSON body for /convert/path."""

    file_path: str = Field(..., description="Absolute path to the input file")
    output_dir: str | None = Field(
        None, description="Base output directory; defaults to parent of file_path"
    )
    engine: str | None = Field(None, description="Engine override")


class ConvertFolderRequest(_MinerUFields):
    """JSON body for /convert/folder."""

    folder_path: str = Field(..., description="Absolute path to the input folder")
    output_dir: str | None = Field(
        None, description="Base output directory; defaults to folder_path"
    )
    engine: str | None = Field(None, description="Engine override")
    recursive: bool = Field(False, description="Include subdirectories while preserving relative output paths")
    extensions: list[str] | None = Field(None, description="Optional supported types, such as ['pdf', 'docx']")


class RetryBatchRequest(BaseModel):
    include_cancelled: bool = Field(False, description="Also resume files cancelled before conversion")


class CancelBatchRequest(BaseModel):
    model_config = {"extra": "forbid"}


def _pick_engine(requested: str | None, file_path: Path) -> str:
    """Pick the engine to use for a conversion request."""
    if requested:
        return requested.lower()
    if DEFAULT_ENGINE not in ("", "auto"):
        return DEFAULT_ENGINE.lower()
    mineru = get_engine("mineru")
    if mineru is not None and file_path.suffix.lower() in mineru.supported_extensions:
        return "mineru"
    return FALLBACK_ENGINE


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialize model configuration and own the persistent batch scheduler."""
    try:
        write_mineru_config()
    except Exception:
        logger.exception("Failed to write MinerU config; continuing")
    BATCH_MANAGER.start()
    try:
        yield
    finally:
        BATCH_MANAGER.close()


app = FastAPI(
    title="docs2md",
    description=(
        "Convert documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, images, etc.) "
        "to Markdown via local engines."
    ),
    version="4.2.0",
    lifespan=_lifespan,
)
app.add_middleware(
    UploadBodyLimitMiddleware,
    limits=RESOURCE_LIMITS,
    max_uploads=RESOURCE_CONFIG.max_concurrent + RESOURCE_CONFIG.max_pending,
)


class HealthResponse(BaseModel):
    status: str
    engines: list[str]
    default_engine: str
    tiers: list[str]
    models_ready: dict[str, bool]
    cuda_available: bool = Field(default=False)
    resources: dict[str, Any]


class ConvertResponse(BaseModel):
    """API response for a single-file conversion.

    Does not include the converted markdown content; callers read the saved file
    from `output_path`.
    """

    success: bool
    engine: str
    output_path: str
    output_dir: str
    images_dir: str | None
    fallback: bool
    fallback_from: str | None = None
    message: str | None


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return service health, engines, and per-tier model readiness."""
    cuda = False
    try:
        import torch

        cuda = torch.cuda.is_available()
    except Exception:
        pass

    tiers = sorted(MINERU_TIERS)
    resources = RESOURCE_CONTROLLER.snapshot()
    resources["limits"].update(asdict(RESOURCE_LIMITS))
    resources["limits"]["max_upload_body_bytes"] = RESOURCE_LIMITS.max_upload_body_bytes
    resources["limits"]["max_uploads"] = RESOURCE_CONFIG.max_concurrent + RESOURCE_CONFIG.max_pending
    resources["batches"] = BATCH_MANAGER.snapshot()
    return HealthResponse(
        status="ok" if list_engines() else "no engines registered",
        engines=list_engines(),
        default_engine=DEFAULT_ENGINE,
        tiers=tiers,
        models_ready={tier: models_look_complete(tier) for tier in tiers},
        cuda_available=cuda,
        resources=resources,
    )


def _run_conversion_uncontrolled(
    file_path: Path,
    *,
    engine_name: str | None = None,
    output_dir: Path | None = None,
    options: ConvertOptions | None = None,
) -> ConvertStatusResponse:
    """Run a single conversion using the requested engine, with fallback."""
    # Recheck after queueing: local inputs can change while a request waits.
    validate_local_file(file_path, RESOURCE_LIMITS)
    chosen = _pick_engine(engine_name, file_path)
    engine_cls = get_engine(chosen)
    if engine_cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {chosen}")

    opts = options or ConvertOptions(output_dir=output_dir)
    if output_dir is not None:
        opts.output_dir = output_dir

    engine = engine_cls()

    # Configuration problems (missing models, missing binary, ...) are
    # client errors: report them without trying a fallback engine.
    try:
        engine.validate_options(opts, file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = engine.convert(file_path, opts)
    except OutputWriteError as exc:
        # A conversion may have completed successfully but still fail while
        # persisting the result (for example, the destination Markdown is
        # locked or the source directory is read-only). Do not run a fallback
        # engine against the same unwritable path.
        logger.exception("Conversion output could not be written")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"{chosen} engine conversion raised an exception")
        result = ConvertResult(
            markdown="",
            engine=chosen,
            output_path="",
            error=f"{chosen} error: {exc}",
        )

    # Fallback to MarkItDown when MinerU fails.
    if result.error and chosen == "mineru" and get_engine("markitdown") is not None:
        mineru_error = result.error
        if opts.mineru_pages != "all":
            raise HTTPException(
                status_code=500,
                detail=(
                    f"{mineru_error}; MarkItDown fallback was skipped because it "
                    "cannot preserve the requested PDF page selection. Retry "
                    "MinerU, or use pages='all' to allow whole-document fallback."
                ),
            )
        logger.warning(f"MinerU failed: {mineru_error}. Falling back to markitdown.")
        fallback = get_engine("markitdown")()
        try:
            result = fallback.convert(file_path, opts)
        except OutputWriteError as exc:
            logger.exception("Fallback output could not be written")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("MarkItDown fallback raised an exception")
            raise HTTPException(
                status_code=500, detail=f"{mineru_error}; markitdown error: {exc}"
            ) from exc
        if result.error:
            result.error = f"{mineru_error}; {result.error}"
        result.engine = "markitdown"
        result.fallback = True
        result.fallback_from = "mineru"

    if result.error:
        raise HTTPException(status_code=500, detail=result.error)

    images_dir = result.images_dir
    if images_dir and not Path(images_dir).is_dir():
        images_dir = None

    return ConvertStatusResponse(
        success=True,
        engine=result.engine,
        output_path=result.output_path,
        output_dir=result.output_dir,
        images_dir=images_dir,
        fallback=result.fallback,
        fallback_from=result.fallback_from,
        message=f"Saved to {result.output_path}",
    )


def _run_conversion(
    file_path: Path,
    *,
    engine_name: str | None = None,
    output_dir: Path | None = None,
    options: ConvertOptions | None = None,
) -> ConvertStatusResponse:
    """Admit one job and run its engines/fallback within one process budget."""
    validate_local_file(file_path, RESOURCE_LIMITS)
    try:
        return RESOURCE_CONTROLLER.convert(
            file_path,
            engine_name=_pick_engine(engine_name, file_path),
            output_dir=output_dir,
            options=options,
        )
    except ResourceBusyError as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc
    except ConversionTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except WorkerHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=exc.headers) from exc
    except WorkerExecutionError as exc:
        logger.exception("Conversion worker failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _options_from_fields(
    *,
    output_dir: Path | None,
    tier: str,
    remote: bool,
    pages: str,
) -> ConvertOptions:
    try:
        return ConvertOptions.from_request(
            output_dir=output_dir,
            tier=tier,
            remote=remote,
            pages=pages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/convert/path", response_model=ConvertResponse)
def convert_path(request: ConvertPathRequest) -> ConvertResponse:
    """Convert a file already on disk."""
    src = Path(request.file_path).expanduser().resolve()
    validate_local_file(src, RESOURCE_LIMITS)

    out_dir = Path(request.output_dir).expanduser().resolve() if request.output_dir else None
    opts = _options_from_fields(
        output_dir=out_dir,
        tier=request.tier,
        remote=request.remote,
        pages=request.pages,
    )
    status = _run_conversion(src, engine_name=request.engine, output_dir=out_dir, options=opts)
    return ConvertResponse.model_validate(asdict(status))


@app.post("/convert/upload", response_model=ConvertResponse)
def convert_upload(
    file: UploadFile = File(...),
    output_dir: str | None = Form(None),
    engine: str | None = Form(None),
    tier: str = Form("standard"),
    remote: bool = Form(False),
    pages: str = Form("all"),
) -> ConvertResponse:
    """Convert an uploaded file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if filename in ("", ".", "..") or "\x00" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid upload filename")

    tmp_dir = Path(tempfile.mkdtemp(prefix="docs2md_upload_"))
    try:
        dest = tmp_dir / filename
        copy_upload_bounded(file.file, dest, RESOURCE_LIMITS)

        # Uploaded files have no meaningful on-disk parent; default to the
        # configured upload output directory so results are not lost when the
        # temp directory is cleaned up.
        if output_dir:
            out_dir = Path(output_dir).expanduser().resolve()
        else:
            out_dir = DEFAULT_UPLOAD_OUTPUT_DIR.expanduser().resolve()
            out_dir.mkdir(parents=True, exist_ok=True)

        opts = _options_from_fields(
            output_dir=out_dir,
            tier=tier,
            remote=remote,
            pages=pages,
        )
        status = _run_conversion(
            dest,
            engine_name=engine,
            output_dir=out_dir,
            options=opts,
        )
        return ConvertResponse.model_validate(asdict(status))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/convert/folder")
def convert_folder(request: ConvertFolderRequest) -> dict[str, Any]:
    """Convert every supported file in a folder."""
    folder, out_dir, opts = _folder_parameters(request)
    plan = _folder_plan(request, folder, out_dir)
    results: list[dict[str, Any]] = []
    for index, item in enumerate(plan.items):
        src = item.path
        try:
            status = _run_conversion(
                src,
                engine_name=request.engine,
                output_dir=item.output_dir,
                options=replace(opts, output_dir=item.output_dir),
            )
            results.append(
                {
                    "file": str(src),
                    "status": "ok",
                    "engine": status.engine,
                    "output_path": status.output_path,
                    "images_dir": status.images_dir,
                    "fallback": status.fallback,
                    "fallback_from": status.fallback_from,
                }
            )
        except HTTPException as exc:
            if exc.status_code == 429:
                # Keep completed work visible even when a synchronous batch
                # cannot acquire another slot; clients can resume pending files.
                raise HTTPException(status_code=429, headers=exc.headers, detail={
                    "message": exc.detail, "folder": str(folder), "output_dir": str(out_dir),
                    "results": results, "summary": _folder_summary(results, len(plan.items)),
                    "pending_files": [str(pending.path) for pending in plan.items[index:]],
                }) from exc
            detail = exc.detail[:2048] if isinstance(exc.detail, str) else exc.detail
            results.append({"file": str(src), "status": "error", "detail": detail, "status_code": exc.status_code})
        except Exception as exc:
            results.append({"file": str(src), "status": "error", "detail": str(exc)[:2048], "status_code": 500})

    return {"folder": str(folder), "output_dir": str(out_dir), "recursive": plan.recursive,
            "results": results, "summary": _folder_summary(results, len(plan.items))}


def _folder_parameters(request: ConvertFolderRequest) -> tuple[Path, Path, ConvertOptions]:
    try:
        folder = Path(request.folder_path).expanduser().resolve()
        output = Path(request.output_dir).expanduser().resolve() if request.output_dir else folder
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid folder or output path") from exc
    options = _options_from_fields(output_dir=output, tier=request.tier, remote=request.remote, pages=request.pages)
    return folder, output, options


def _folder_plan(request: ConvertFolderRequest, folder: Path, output: Path) -> BatchInputPlan:
    return plan_folder_inputs(folder, all_supported_extensions(), RESOURCE_LIMITS,
                              recursive=request.recursive, output_dir=output, extensions=request.extensions)


def _folder_summary(results: list[dict[str, Any]], total: int) -> dict[str, int]:
    succeeded = sum(item["status"] == "ok" for item in results)
    return {"total": total, "processed": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "pending": total - len(results)}


def _batch_identity(request: ConvertFolderRequest, folder: Path, output: Path) -> dict[str, Any]:
    extensions = None
    if request.extensions is not None:
        extensions = sorted({("" if value.strip().startswith(".") else ".") + value.strip().lower()
                             for value in request.extensions})
    return {"folder_path": str(folder), "output_dir": str(output), "recursive": request.recursive,
            "extensions": extensions, "engine": request.engine.lower() if request.engine else None,
            "tier": request.tier, "remote": request.remote, "pages": request.pages}


@app.post("/batches", status_code=202)
def submit_batch(request: ConvertFolderRequest, response: Response,
                 idempotency_key: str | None = Header(None, max_length=128)) -> dict[str, Any]:
    """Accept a persistent batch; poll its Location for progress and results."""
    folder, output, options = _folder_parameters(request)
    identity = _batch_identity(request, folder, output)
    if idempotency_key is not None:
        if not idempotency_key or any(not 33 <= ord(character) <= 126 for character in idempotency_key):
            raise HTTPException(status_code=400, detail="Idempotency-Key must contain visible ASCII characters")
        existing = BATCH_MANAGER.lookup_idempotent(idempotency_key, identity)
        if existing is not None:
            response.headers["Location"] = f"/batches/{existing['job_id']}"
            return existing
    plan = _folder_plan(request, folder, output)
    for item in plan.items:
        selected = _pick_engine(request.engine, item.path)
        if get_engine(selected) is None:
            raise HTTPException(status_code=400, detail=f"Unknown engine: {selected}"[:2048])
    if request.engine and get_engine(request.engine.lower()) is None:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {request.engine}"[:2048])
    job = BATCH_MANAGER.submit(plan, engine_name=request.engine.lower() if request.engine else None,
                               options=options, idempotency_key=idempotency_key, request_identity=identity)
    response.headers["Location"] = f"/batches/{job['job_id']}"
    return job


@app.get("/batches")
def list_batches(limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    return {"jobs": BATCH_MANAGER.list_jobs(limit=limit), "limit": limit}


@app.get("/batches/{job_id}")
def get_batch(job_id: str) -> dict[str, Any]:
    return BATCH_MANAGER.get(job_id)


@app.post("/batches/{job_id}/cancel")
def cancel_batch(job_id: str, request: CancelBatchRequest | None = None) -> dict[str, Any]:
    """Stop future items; let an already scheduled file finish safely."""
    # Parsing the optional empty body also enforces the streaming JSON limit.
    return BATCH_MANAGER.cancel(job_id)


@app.post("/batches/{job_id}/retry", status_code=202)
def retry_batch(job_id: str, response: Response, request: RetryBatchRequest | None = None) -> dict[str, Any]:
    job = BATCH_MANAGER.retry(job_id, include_cancelled=request.include_cancelled if request else False)
    response.headers["Location"] = f"/batches/{job_id}"
    return job


def _batch_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw) if raw.isascii() and raw.isdecimal() else 0
    except ValueError:
        value = 0
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


# Constructing a manager has no filesystem or scheduling effects. Isolated
# conversion workers import this module too; only the API lifespan starts it.
BATCH_MANAGER = BatchJobManager(
    Path(os.environ.get("DOCS2MD_BATCH_STATE_DIR") or PROJECT_ROOT / ".docs2md-state").expanduser().resolve(),
    lambda path, **kwargs: _run_conversion(path, **kwargs),
    max_active_jobs=_batch_limit("DOCS2MD_MAX_BATCH_JOBS", 5),
    max_history_jobs=_batch_limit("DOCS2MD_BATCH_HISTORY_LIMIT", 100),
    select_engine=lambda path: _pick_engine(None, path),
)


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "docs2md service is running. POST to /convert/path, /convert/upload, /convert/folder, or /batches."


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
