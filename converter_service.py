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
import re
import shutil
import subprocess
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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from loguru import logger
from markitdown import MarkItDown
from pydantic import BaseModel

app = FastAPI(
    title="Document to Markdown Converter",
    description="Convert PDF, DOCX, PPTX, XLSX, and other formats to Markdown",
    version="2.2.0",
)

markitdown = MarkItDown()

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".html", ".htm", ".csv", ".json",
    ".xml", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
    ".mp3", ".wav", ".ogg", ".wma", ".m4a", ".flac",
    ".zip",
}

# Characters that can follow a backslash to form a valid JSON escape sequence.
_VALID_JSON_ESCAPES = frozenset('"\\/bfnrtu')


def _fix_json_backslashes(raw_body: bytes) -> bytes:
    """Fix single backslashes in JSON string values so the body is valid JSON.

    Windows file paths like ``C:\\Users\\...`` contain backslashes that are
    illegal in JSON string literals (e.g. ``\\U``, ``\\P``, ``\\d``).  This
    function walks through the raw JSON bytes tracking whether we are inside a
    string and doubles any backslash that isn't already part of a valid JSON
    escape sequence.

    Already-correct JSON is returned unchanged.
    """
    body = raw_body.decode("utf-8")
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"' and (i == 0 or body[i - 1] != '\\'):
            # Toggle string state on unescaped double-quote
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == '\\':
            # Inside a string: check if the next character forms a valid escape
            if i + 1 < len(body) and body[i + 1] in _VALID_JSON_ESCAPES:
                # Valid escape (e.g. \\n, \\t, \\\", \\\\) — leave as-is
                result.append(ch)
            else:
                # Not a valid escape — double the backslash so it becomes a
                # literal backslash in the parsed JSON string (e.g. C:\\Users)
                result.append('\\\\')
        else:
            result.append(ch)
        i += 1
    return ''.join(result).encode("utf-8")

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


def _resolve_output_dir(source_path: str | Path, output_dir: str | None) -> Path:
    """Resolve the output directory for a given source path.

    If *output_dir* is explicitly given it is used as-is.
    Otherwise the default is ``<source_parent>/docs2md/`` (for files)
    or ``<source>/docs2md/`` (for folders).
    """
    if output_dir:
        return Path(output_dir)

    source = Path(source_path)
    if source.is_file():
        return source.parent / "docs2md"
    else:
        return source / "docs2md"


def _save_markdown(text: str, output_dir: Path, stem: str, images_dir: Path | None = None) -> str:
    """Write markdown text to ``<output_dir>/<stem>.md``, creating dirs as needed.

    If *images_dir* is given, also copies its contents into
    ``<output_dir>/images/`` so that relative image references in the
    markdown (e.g. ``![](images/foo.jpg)``) resolve correctly.

    Returns the absolute path of the written .md file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{stem}.md"
    out_path.write_text(text, encoding="utf-8")

    if images_dir is not None and images_dir.is_dir():
        dest_images = output_dir / images_dir.name
        if dest_images.exists():
            shutil.rmtree(dest_images, ignore_errors=True)
            # On Windows, rmtree with ignore_errors may leave the directory
            # behind (e.g. due to AV locks).  Retry once after a short wait.
            if dest_images.exists():
                import time
                time.sleep(0.3)
                shutil.rmtree(dest_images, ignore_errors=True)
        if not dest_images.exists():
            shutil.copytree(images_dir, dest_images)
            logger.info(f"Copied images to {dest_images}")
        else:
            logger.warning(
                f"Could not remove existing images directory {dest_images}; "
                f"skipping image copy"
            )

    return str(out_path.resolve())


def convert_with_markitdown(file_path: str | Path) -> str:
    """Convert a file to Markdown using MarkItDown."""
    result = markitdown.convert(str(file_path))
    return result.text_content.strip()


def _find_images_dir(near_dir: Path, search_root: Path) -> Path | None:
    """Find the ``images/`` directory in the MinerU output tree.

    Looks first next to *near_dir* (the typical location), then falls back
    to a recursive search under *search_root* for robustness against
    different MinerU output layouts.
    """
    candidate = near_dir / "images"
    if candidate.is_dir():
        return candidate
    # Fallback: search the entire output tree for an images directory
    for root, dirs, _files in os.walk(search_root):
        if "images" in dirs:
            return Path(root) / "images"
    return None


def convert_pdf_with_mineru(
    pdf_path: str | Path,
    method: str = "ocr",
    lang: str | None = None,
    formula_enable: bool = True,
    table_enable: bool = True,
) -> tuple[str, Path | None, Path]:
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

    Returns:
        ``(markdown_text, images_dir, work_dir)`` where *images_dir* is the
        path to extracted images inside the temporary *work_dir* (may be
        ``None`` if no images were produced), and *work_dir* must be cleaned
        up by the caller after images have been copied out.

    Raises RuntimeError if the MinerU CLI is not found or conversion fails.
    """
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
    mineru_out_dir = work_dir / "output"
    os.makedirs(mineru_out_dir, exist_ok=True)

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
        "-o", str(mineru_out_dir),
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
        # MinerU v3 output: <mineru_out_dir>/<file_name>/<method>/<file_name>.md
        md_dir = mineru_out_dir / file_name / method
        md_file = md_dir / f"{file_name}.md"
        if not md_file.exists():
            # Fallback: search for any .md in the output tree
            for root, _dirs, files in os.walk(mineru_out_dir):
                for f in files:
                    if f.endswith(".md"):
                        md_file = Path(root) / f
                        break

        if not md_file.exists():
            raise RuntimeError(
                "MinerU completed but no .md output file was found "
                f"in {mineru_out_dir}"
            )

        text = md_file.read_text(encoding="utf-8").strip()

        # Replace short filename references with original name in output
        if _tmp_pdf is not None and file_name != original_name:
            text = text.replace(file_name, original_name)

        # Locate the images directory produced by MinerU.
        # The caller is responsible for copying images out of work_dir
        # and cleaning up work_dir afterwards.
        images_source = _find_images_dir(md_file.parent, mineru_out_dir)

        return text, images_source, work_dir

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"MinerU CLI timed out after 600 seconds processing '{file_name}'"
        )
    except Exception:
        # On error, clean up work_dir before re-raising
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def convert_file(
    file_path: str | Path,
    use_mineru_for_pdf: bool = True,
    mineru_method: str = "ocr",
    mineru_lang: str | None = None,
    output_dir: str | None = None,
) -> tuple[str, str, str | None]:
    """
    Convert a single file to Markdown.

    Returns ``(markdown_text, engine_used, output_path)``.

    If *output_dir* is given (or defaults to ``<parent>/docs2md/``), the
    result is also written to disk at ``<output_dir>/<stem>.md``.
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

    written_path: str | None = None
    resolved_out = _resolve_output_dir(file_path, output_dir)

    # PDF — use MinerU with MarkItDown fallback
    if ext == ".pdf" and use_mineru_for_pdf:
        mineru_work_dir: Path | None = None
        try:
            md, images_dir, mineru_work_dir = convert_pdf_with_mineru(
                file_path,
                method=mineru_method,
                lang=mineru_lang,
                formula_enable=True,
                table_enable=True,
            )
            written_path = _save_markdown(
                md, resolved_out, file_path.stem, images_dir=images_dir,
            )
            return md, "mineru", written_path
        except Exception as mineru_err:
            logger.warning(
                f"MinerU failed, falling back to MarkItDown: {mineru_err}"
            )
            try:
                md = convert_with_markitdown(file_path)
                written_path = _save_markdown(md, resolved_out, file_path.stem)
                return md, "markitdown", written_path
            except Exception as md_err:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"MinerU error: {mineru_err}. "
                        f"MarkItDown fallback error: {md_err}"
                    ),
                )
        finally:
            if mineru_work_dir is not None:
                shutil.rmtree(mineru_work_dir, ignore_errors=True)

    # Non-PDF or MinerU disabled — use MarkItDown directly
    try:
        md = convert_with_markitdown(file_path)
        written_path = _save_markdown(md, resolved_out, file_path.stem)
        return md, "markitdown", written_path
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"MarkItDown conversion failed: {e}",
        )


def convert_folder(
    folder_path: str | Path,
    output_dir: str | None = None,
    use_mineru_for_pdf: bool = True,
    mineru_method: str = "ocr",
    mineru_lang: str | None = None,
    recursive: bool = True,
) -> list[dict]:
    """
    Convert all supported files in a folder to Markdown.

    Returns a list of result dicts, one per file:
    ``{file_path, stem, engine, output_path, status, error}``
    """
    folder_path = Path(folder_path)
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    if not folder_path.is_dir():
        raise ValueError(f"Path is not a directory: {folder_path}")

    resolved_out = _resolve_output_dir(folder_path, output_dir)
    results: list[dict] = []

    pattern = "**/*" if recursive else "*"
    candidates = sorted(
        p for p in folder_path.glob(pattern)
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not candidates:
        logger.info(f"No supported files found in {folder_path}")
        return results

    logger.info(f"Batch converting {len(candidates)} file(s) in {folder_path}")

    for file_path in candidates:
        rel = file_path.relative_to(folder_path)
        file_out_dir = resolved_out / rel.parent
        try:
            md, engine, _ = convert_file(
                file_path,
                use_mineru_for_pdf=use_mineru_for_pdf,
                mineru_method=mineru_method,
                mineru_lang=mineru_lang,
                output_dir=str(file_out_dir),
            )
            results.append({
                "file_path": str(file_path),
                "stem": file_path.stem,
                "engine": engine,
                "output_path": str(file_out_dir / f"{file_path.stem}.md"),
                "status": "success",
                "error": None,
            })
        except Exception as exc:
            logger.error(f"Failed to convert {file_path}: {exc}")
            results.append({
                "file_path": str(file_path),
                "stem": file_path.stem,
                "engine": None,
                "output_path": None,
                "status": "failed",
                "error": str(exc),
            })

    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    logger.info(
        f"Batch complete: {succeeded} succeeded, {failed} failed "
        f"out of {len(results)} total"
    )
    return results


# ── API Models ────────────────────────────────────────────────────────────

class PathRequest(BaseModel):
    file_path: str
    use_mineru_for_pdf: bool = True
    mineru_method: str = "ocr"
    mineru_lang: str | None = None
    output_dir: str | None = None


class FolderRequest(BaseModel):
    folder_path: str
    output_dir: str | None = None
    use_mineru_for_pdf: bool = True
    mineru_method: str = "ocr"
    mineru_lang: str | None = None
    recursive: bool = True


class ConvertResponse(BaseModel):
    status: str
    markdown: str | None = None
    engine: str | None = None
    output_path: str | None = None
    detail: str | None = None


class FileResult(BaseModel):
    file_path: str
    stem: str
    engine: str | None = None
    output_path: str | None = None
    status: str
    error: str | None = None


class FolderConvertResponse(BaseModel):
    status: str
    total: int
    succeeded: int
    failed: int
    results: list[FileResult] = []


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
async def convert_by_path(request: Request):
    """Convert a single file by its local absolute path to Markdown.

    Accepts Windows-style paths with single backslashes
    (e.g. ``C:\\Users\\...``) in addition to forward-slash paths.
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Body may contain Windows paths with unescaped backslashes
        data = json.loads(_fix_json_backslashes(body))

    req = PathRequest(**data)
    try:
        md, engine, out_path = convert_file(
            req.file_path,
            use_mineru_for_pdf=req.use_mineru_for_pdf,
            mineru_method=req.mineru_method,
            mineru_lang=req.mineru_lang,
            output_dir=req.output_dir,
        )
        return ConvertResponse(
            status="success", markdown=md, engine=engine, output_path=out_path,
        )
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
    output_dir: str | None = Form(None),
):
    """Upload a file and convert it to Markdown."""
    suffix = Path(file.filename or "upload").suffix
    tmp_path = Path(tempfile.gettempdir()) / f"doc2md_{uuid.uuid4().hex}{suffix}"

    try:
        content = await file.read()
        tmp_path.write_bytes(content)
        md, engine, out_path = convert_file(
            tmp_path,
            use_mineru_for_pdf=use_mineru_for_pdf,
            mineru_method=mineru_method,
            mineru_lang=mineru_lang,
            output_dir=output_dir,
        )
        return ConvertResponse(
            status="success", markdown=md, engine=engine, output_path=out_path,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@app.post("/convert/folder", response_model=FolderConvertResponse)
async def convert_by_folder(request: Request):
    """Batch-convert all supported files in a folder to Markdown.

    Accepts Windows-style paths with single backslashes
    (e.g. ``C:\\Users\\...``) in addition to forward-slash paths.
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = json.loads(_fix_json_backslashes(body))

    req = FolderRequest(**data)
    try:
        results = convert_folder(
            req.folder_path,
            output_dir=req.output_dir,
            use_mineru_for_pdf=req.use_mineru_for_pdf,
            mineru_method=req.mineru_method,
            mineru_lang=req.mineru_lang,
            recursive=req.recursive,
        )
        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")
        return FolderConvertResponse(
            status="success" if failed == 0 else "partial",
            total=len(results),
            succeeded=succeeded,
            failed=failed,
            results=[FileResult(**r) for r in results],
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
