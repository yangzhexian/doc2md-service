"""
Document to Markdown Converter Service
======================================
A FastAPI-based microservice that converts PDF, DOCX, PPTX, XLSX, HTML, CSV,
and image files to Markdown using MinerU and MarkItDown.

PDF files are processed with MinerU using GPU-accelerated
OCR mode for high-quality formula, table, and image recognition,
with automatic fallback to MarkItDown on failure.
All other formats are processed directly with MarkItDown.
"""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

# ── MinerU model path configuration ─────────────────────────────────────────
# These MUST be set before importing magic_pdf / mineru modules.
# MinerU reads environment variables at import time to locate model files.

_PROJECT_ROOT = Path(__file__).resolve().parent
_MINERU_MODELS_DIR = _PROJECT_ROOT / "mineru_models"

if not _MINERU_MODELS_DIR.exists():
    raise FileNotFoundError(
        f"MinerU models directory not found: {_MINERU_MODELS_DIR}\n"
        "Please download models first: mineru-models-download\n"
        "Then copy the downloaded models to the mineru_models/ directory."
    )

os.environ["MINERU_MODEL_SOURCE"] = "local"

# Build a runtime config with the correct absolute models path and write it
# to the user home directory.  Both magic_pdf (v1.x) and mineru (v3.x) read
# from magic-pdf.json / mineru.json in the user home at import time.
_user_home = Path.home()

# magic_pdf (v1.x) expects "models-dir" as a plain string.
_magic_pdf_config = {"models-dir": str(_MINERU_MODELS_DIR)}
(_user_home / "magic-pdf.json").write_text(
    json.dumps(_magic_pdf_config, indent=2), encoding="utf-8"
)

# mineru (v3.x) reads mineru.json and expects models-dir as a dict
# with "pipeline" (and optionally "vlm") keys.
_mineru_runtime_config = json.loads(
    (_PROJECT_ROOT / "mineru.json").read_text(encoding="utf-8")
)
_mineru_runtime_config["models-dir"] = {
    "pipeline": str(_MINERU_MODELS_DIR),
    "vlm": "",
}
(_user_home / "mineru.json").write_text(
    json.dumps(_mineru_runtime_config, indent=2), encoding="utf-8"
)

# Point MinerU at the config we just wrote (in user home).
# NOTE: magic_pdf uses MINERU_TOOLS_CONFIG_JSON to locate its config file.
os.environ["MINERU_TOOLS_CONFIG_JSON"] = str(_user_home / "mineru.json")

os.environ["FLAGS_npu_jit_compile"] = "0"
os.environ["FLAGS_use_stride_kernel"] = "0"
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from loguru import logger
from markitdown import MarkItDown
from pydantic import BaseModel

app = FastAPI(
    title="Document to Markdown Converter",
    description="Convert PDF, DOCX, PPTX, XLSX, and other formats to Markdown",
    version="2.1.0",
)

markitdown = MarkItDown()

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".html", ".htm", ".csv", ".json",
    ".xml", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
    ".mp3", ".wav", ".ogg", ".wma", ".m4a", ".flac",
    ".zip",
}

# Windows MAX_PATH is 260 chars. MinerU creates deep nested temp directories
# (output_dir / filename / method / images / ...).  Keep the working stem
# short so the full path stays well under the limit.
_MAX_SAFE_STEM_LENGTH = 40


def _needs_short_name(file_stem: str) -> bool:
    """Return True if the file stem is long enough to risk MAX_PATH issues."""
    return len(file_stem) > _MAX_SAFE_STEM_LENGTH


def _sanitize_filename(name: str) -> str:
    """Replace path-unsafe characters with underscores."""
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    return name


def convert_with_markitdown(file_path: str | Path) -> str:
    """Convert a file to Markdown using MarkItDown."""
    result = markitdown.convert(str(file_path))
    return result.text_content.strip()


def convert_pdf_with_mineru(
    pdf_path: str | Path,
    method: str = "ocr",
    lang: str | None = None,
    formula_enable: bool = True,
    table_enable: bool = True,
) -> str:
    """
    Convert a PDF to Markdown using the official MinerU CLI (v3.x).

    Uses ``mineru`` with the ``pipeline`` backend for high-quality local
    parsing with GPU acceleration, formula recognition, and table extraction.

    To avoid Windows MAX_PATH issues, PDFs with long filenames are
    automatically copied to a short temporary name before processing.

    Args:
        pdf_path: Path to the PDF file.
        method: Parse method — 'ocr', 'txt', or 'auto'.
        lang: Language code for OCR (e.g. 'en', 'ch'). None for auto-detect.
        formula_enable: Enable formula recognition.
        table_enable: Enable table structure recognition.

    Raises RuntimeError if the MinerU CLI is not found or conversion fails.
    """
    import subprocess

    pdf_path = Path(pdf_path)
    original_name = pdf_path.stem
    work_dir = Path(tempfile.mkdtemp(prefix="mineru_"))

    # ── Handle long filenames (Windows MAX_PATH workaround) ─────────────
    _tmp_pdf: Path | None = None
    if _needs_short_name(original_name):
        safe_name = _sanitize_filename(original_name)
        short_name = safe_name[:30]  # keep it brief
        _tmp_pdf = work_dir / f"{short_name}.pdf"
        shutil.copy2(pdf_path, _tmp_pdf)
        logger.info(
            f"MinerU: filename '{original_name}' is too long "
            f"({len(original_name)} chars). Using short copy: {_tmp_pdf.name}"
        )
        pdf_path = _tmp_pdf

    file_name = pdf_path.stem
    output_dir = work_dir / "output"
    os.makedirs(output_dir, exist_ok=True)

    # Locate the mineru CLI (installed as a console script by the package).
    mineru_bin = shutil.which("mineru")
    if mineru_bin is None:
        raise RuntimeError(
            "MinerU CLI not found in PATH. "
            "Install it with: pip install 'mineru[all]'"
        )

    # Build CLI arguments (see: https://opendatalab.github.io/MinerU/usage/)
    cmd: list[str] = [
        mineru_bin,
        "-p", str(pdf_path),
        "-o", str(output_dir),
        "-b", "pipeline",
        "-m", method,
    ]
    if lang:
        cmd.extend(["-l", lang])
    else:
        cmd.extend(["-l", "en"])  # default to English for academic papers
    if not formula_enable:
        cmd.append("-f false")
    if not table_enable:
        cmd.append("-t false")

    logger.info(
        f"MinerU CLI: parsing '{file_name}' (original: '{original_name}') "
        f"with method={method}, formula_enable={formula_enable}, "
        f"table_enable={table_enable}, lang={lang or 'en'}"
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10-minute timeout for large PDFs
            env={**os.environ, "MINERU_MODEL_SOURCE": "local"},
        )

        if result.returncode != 0:
            stderr_summary = result.stderr.strip()[-500:] if result.stderr else ""
            raise RuntimeError(
                f"MinerU CLI exited with code {result.returncode}.\n"
                f"STDERR: {stderr_summary}"
            )

        # Locate the generated .md file.
        # MinerU v3 output structure: <output_dir>/<file_name>/<method>/<file_name>.md
        md_dir = output_dir / file_name / method
        md_file = md_dir / f"{file_name}.md"
        if not md_file.exists():
            # Fallback: search for any .md in the output tree
            for root, _dirs, files in os.walk(output_dir):
                for f in files:
                    if f.endswith(".md"):
                        md_file = Path(root) / f
                        break

        if not md_file.exists():
            raise RuntimeError(
                "MinerU completed but no .md output file was found "
                f"in {output_dir}"
            )

        text = md_file.read_text(encoding="utf-8").strip()

        # Replace short filename references with original name in output
        if _tmp_pdf is not None and file_name != original_name:
            text = text.replace(file_name, original_name)

        return text

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"MinerU CLI timed out after 600 seconds processing '{file_name}'"
        )
    except Exception:
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def convert_file(
    file_path: str | Path,
    use_mineru_for_pdf: bool = True,
    mineru_method: str = "ocr",
    mineru_lang: str | None = None,
) -> tuple[str, str]:
    """
    Convert a file to Markdown.

    Returns (markdown_text, engine_used) where engine_used is
    'mineru' or 'markitdown'.

    For PDF files, MinerU is used by default with MarkItDown as fallback.
    All other formats are processed directly with MarkItDown.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Path is not a regular file: {file_path}")

    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # PDF — use MinerU with MarkItDown fallback
    if ext == ".pdf" and use_mineru_for_pdf:
        try:
            md = convert_pdf_with_mineru(
                file_path,
                method=mineru_method,
                lang=mineru_lang,
                formula_enable=True,
                table_enable=True,
            )
            return md, "mineru"
        except Exception as mineru_err:
            logger.warning(
                f"MinerU failed, falling back to MarkItDown: {mineru_err}"
            )
            try:
                return convert_with_markitdown(file_path), "markitdown"
            except Exception as md_err:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"MinerU error: {mineru_err}. "
                        f"MarkItDown fallback error: {md_err}"
                    ),
                )

    # Non-PDF or MinerU disabled — use MarkItDown directly
    try:
        return convert_with_markitdown(file_path), "markitdown"
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"MarkItDown conversion failed: {e}",
        )


# ── API Models ────────────────────────────────────────────────────────────

class PathRequest(BaseModel):
    file_path: str
    use_mineru_for_pdf: bool = True
    mineru_method: str = "ocr"
    mineru_lang: str | None = None


class ConvertResponse(BaseModel):
    status: str
    markdown: str | None = None
    engine: str | None = None
    detail: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    gpu_available = False
    try:
        import torch
        gpu_available = torch.cuda.is_available()
    except Exception:
        pass
    return {
        "status": "ok",
        "mineru_available": True,
        "gpu_available": gpu_available,
    }


@app.post("/convert/path", response_model=ConvertResponse)
async def convert_by_path(req: PathRequest):
    """Convert a file at the given local absolute path to Markdown."""
    try:
        result, engine = convert_file(
            req.file_path,
            use_mineru_for_pdf=req.use_mineru_for_pdf,
            mineru_method=req.mineru_method,
            mineru_lang=req.mineru_lang,
        )
        return ConvertResponse(status="success", markdown=result, engine=engine)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/convert/upload", response_model=ConvertResponse)
async def convert_by_upload(
    file: UploadFile = File(...),
    use_mineru_for_pdf: bool = Form(True),
    mineru_method: str = Form("ocr"),
    mineru_lang: str | None = Form(None),
):
    """Upload a file and convert it to Markdown."""
    suffix = Path(file.filename or "upload").suffix
    tmp_path = Path(tempfile.gettempdir()) / f"doc2md_{uuid.uuid4().hex}{suffix}"

    try:
        content = await file.read()
        tmp_path.write_bytes(content)
        result, engine = convert_file(
            tmp_path,
            use_mineru_for_pdf=use_mineru_for_pdf,
            mineru_method=mineru_method,
            mineru_lang=mineru_lang,
        )
        return ConvertResponse(status="success", markdown=result, engine=engine)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
