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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from loguru import logger
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from engines import ConvertOptions, ConvertStatusResponse, get_engine, list_engines  # noqa: E402
from model_manager import pipeline_models_look_complete, write_runtime_configs  # noqa: E402

DEFAULT_ENGINE = os.environ.get("DOCS2MD_ENGINE", "mineru")
# Uploaded files have no natural parent directory on the server, so they land
# here unless the caller supplies an output_dir.
DEFAULT_UPLOAD_OUTPUT_DIR = Path(
    os.environ.get("DOCS2MD_UPLOAD_OUTPUT_DIR") or PROJECT_ROOT / "output"
)


class ConvertPathRequest(BaseModel):
    """JSON body for /convert/path."""

    file_path: str = Field(..., description="Absolute path to the input file")
    output_dir: str | None = Field(
        None, description="Base output directory; defaults to parent of file_path"
    )
    engine: str | None = Field(None, description="Engine override")
    method: str = Field("auto", description="MinerU parse method: auto, ocr, txt")
    lang: str = Field("", description="MinerU language hint")
    formula_enable: bool = Field(True, description="MinerU formula recognition")
    table_enable: bool = Field(True, description="MinerU table recognition")


class ConvertFolderRequest(BaseModel):
    """JSON body for /convert/folder."""

    folder_path: str = Field(..., description="Absolute path to the input folder")
    output_dir: str | None = Field(
        None, description="Base output directory; defaults to folder_path"
    )
    engine: str | None = Field(None, description="Engine override")
    method: str = Field("auto", description="MinerU parse method: auto, ocr, txt")
    lang: str = Field("", description="MinerU language hint")
    formula_enable: bool = Field(True, description="MinerU formula recognition")
    table_enable: bool = Field(True, description="MinerU table recognition")


def _pick_engine(requested: str | None, file_path: Path) -> str:
    """Pick the engine to use for a conversion request."""
    if requested:
        return requested.lower()

    if DEFAULT_ENGINE == "auto":
        if file_path.suffix.lower() == ".pdf":
            return "mineru"
        return "markitdown"

    return DEFAULT_ENGINE.lower()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Write runtime MinerU configs on startup and point MinerU at them."""
    try:
        write_runtime_configs()
        os.environ["MINERU_TOOLS_CONFIG_JSON"] = str(PROJECT_ROOT / "config" / "mineru.json")
    except Exception:
        logger.exception("Failed to write runtime model configs; continuing")
    yield


app = FastAPI(
    title="docs2md",
    description="Convert documents (PDF, DOCX, PPTX, XLSX, images, etc.) to Markdown via local engines.",
    version="3.5.0",
    lifespan=_lifespan,
)


class HealthResponse(BaseModel):
    status: str
    engines: list[str]
    default_engine: str
    models_ready: bool
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
    message: str | None


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return service health and available engines."""
    cuda = False
    try:
        import torch

        cuda = torch.cuda.is_available()
    except Exception:
        pass

    engines = list_engines()
    return HealthResponse(
        status="ok" if engines else "no engines registered",
        engines=engines,
        default_engine=DEFAULT_ENGINE,
        models_ready=pipeline_models_look_complete(),
        cuda_available=cuda,
    )


def _build_options(
    *,
    method: str = "auto",
    lang: str = "",
    formula_enable: bool = True,
    table_enable: bool = True,
) -> dict[str, Any]:
    return {
        "method": method,
        "lang": lang,
        "formula_enable": formula_enable,
        "table_enable": table_enable,
    }


def _run_conversion(
    file_path: Path,
    *,
    engine_name: str | None = None,
    output_dir: Path | None = None,
    options: dict[str, Any] | None = None,
) -> ConvertStatusResponse:
    """Run a single conversion using the requested engine, with fallback."""
    chosen = _pick_engine(engine_name, file_path)
    engine_cls = get_engine(chosen)
    if engine_cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {chosen}")

    opts = ConvertOptions.from_request(
        output_dir=output_dir,
        extra=options or {},
        method=(options or {}).get("method", "auto"),
        lang=(options or {}).get("lang", ""),
        formula_enable=(options or {}).get("formula_enable", True),
        table_enable=(options or {}).get("table_enable", True),
    )

    engine = engine_cls()
    result = engine.convert(file_path, opts)

    # Fallback to MarkItDown when MinerU fails.
    if result.error and chosen == "mineru" and get_engine("markitdown") is not None:
        logger.warning(f"MinerU failed: {result.error}. Falling back to markitdown.")
        fallback = get_engine("markitdown")()
        result = fallback.convert(file_path, opts)
        result.engine = "markitdown (fallback from mineru)"
        result.fallback = True

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
        message=f"Saved to {result.output_path}",
    )


@app.post("/convert/path", response_model=ConvertResponse)
def convert_path(request: ConvertPathRequest) -> ConvertResponse:
    """Convert a file already on disk."""
    src = Path(request.file_path).expanduser().resolve()
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {src}")

    out_dir = Path(request.output_dir).expanduser().resolve() if request.output_dir else None
    options = _build_options(
        method=request.method,
        lang=request.lang,
        formula_enable=request.formula_enable,
        table_enable=request.table_enable,
    )
    status = _run_conversion(src, engine_name=request.engine, output_dir=out_dir, options=options)
    return ConvertResponse(**status.__dict__)


@app.post("/convert/upload", response_model=ConvertResponse)
def convert_upload(
    file: UploadFile = File(...),
    output_dir: str | None = Form(None),
    engine: str | None = Form(None),
    method: str = Form("auto"),
    lang: str = Form(""),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True),
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
        out_dir: Path | None = None
        if output_dir:
            out_dir = Path(output_dir).expanduser().resolve()
        else:
            out_dir = DEFAULT_UPLOAD_OUTPUT_DIR.expanduser().resolve()
            out_dir.mkdir(parents=True, exist_ok=True)

        options = _build_options(
            method=method,
            lang=lang,
            formula_enable=formula_enable,
            table_enable=table_enable,
        )
        status = _run_conversion(
            dest,
            engine_name=engine,
            output_dir=out_dir,
            options=options,
        )
        return ConvertResponse(**status.__dict__)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/convert/folder")
def convert_folder(request: ConvertFolderRequest) -> dict[str, Any]:
    """Convert every supported file in a folder."""
    folder = Path(request.folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")

    out_dir = Path(request.output_dir).expanduser().resolve() if request.output_dir else folder
    options = _build_options(
        method=request.method,
        lang=request.lang,
        formula_enable=request.formula_enable,
        table_enable=request.table_enable,
    )

    results: list[dict[str, Any]] = []
    supported = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".csv", ".png", ".jpg", ".jpeg")
    for src in sorted(folder.iterdir()):
        if src.is_file() and src.suffix.lower() in supported:
            try:
                status = _run_conversion(
                    src,
                    engine_name=request.engine,
                    output_dir=out_dir,
                    options=options,
                )
                results.append(
                    {
                        "file": str(src),
                        "status": "ok",
                        "engine": status.engine,
                        "output_path": status.output_path,
                        "images_dir": status.images_dir,
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
