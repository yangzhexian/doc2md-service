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
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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

# "auto" routes by extension; a concrete name forces that engine.
DEFAULT_ENGINE = os.environ.get("DOCS2MD_ENGINE", "auto")
# Engine used when the file type is not claimed by MinerU.
FALLBACK_ENGINE = os.environ.get("DOCS2MD_FALLBACK_ENGINE", "markitdown")
# Uploaded files have no natural parent directory on the server, so they land
# here unless the caller supplies an output_dir.
DEFAULT_UPLOAD_OUTPUT_DIR = Path(
    os.environ.get("DOCS2MD_UPLOAD_OUTPUT_DIR") or PROJECT_ROOT / "output"
)


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
    """Write the MinerU 4.x config once on startup."""
    try:
        write_mineru_config()
    except Exception:
        logger.exception("Failed to write MinerU config; continuing")
    yield


app = FastAPI(
    title="docs2md",
    description=(
        "Convert documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, images, etc.) "
        "to Markdown via local engines."
    ),
    version="4.0.0",
    lifespan=_lifespan,
)


class HealthResponse(BaseModel):
    status: str
    engines: list[str]
    default_engine: str
    tiers: list[str]
    models_ready: dict[str, bool]
    cuda_available: bool = Field(default=False)


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
    return HealthResponse(
        status="ok" if list_engines() else "no engines registered",
        engines=list_engines(),
        default_engine=DEFAULT_ENGINE,
        tiers=tiers,
        models_ready={tier: models_look_complete(tier) for tier in tiers},
        cuda_available=cuda,
    )


def _run_conversion(
    file_path: Path,
    *,
    engine_name: str | None = None,
    output_dir: Path | None = None,
    options: ConvertOptions | None = None,
) -> ConvertStatusResponse:
    """Run a single conversion using the requested engine, with fallback."""
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
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {src}")

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

    tmp_dir = Path(tempfile.mkdtemp(prefix="docs2md_upload_"))
    try:
        dest = tmp_dir / Path(file.filename).name
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)

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
    folder = Path(request.folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")

    out_dir = Path(request.output_dir).expanduser().resolve() if request.output_dir else folder
    opts = _options_from_fields(
        output_dir=out_dir,
        tier=request.tier,
        remote=request.remote,
        pages=request.pages,
    )

    results: list[dict[str, Any]] = []
    supported = all_supported_extensions()
    for src in sorted(folder.iterdir()):
        if src.is_file() and src.suffix.lower() in supported:
            try:
                status = _run_conversion(
                    src,
                    engine_name=request.engine,
                    output_dir=out_dir,
                    options=opts,
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
                results.append({"file": str(src), "status": "error", "detail": exc.detail})
            except Exception as exc:
                results.append({"file": str(src), "status": "error", "detail": str(exc)})

    return {"folder": str(folder), "output_dir": str(out_dir), "results": results}


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "docs2md service is running. POST to /convert/path, /convert/upload, or /convert/folder."


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
