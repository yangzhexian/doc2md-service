"""MinerU converter engine.

Runs the official MinerU CLI for PDF conversion, with automatic fallback
disabled at this layer — the service handles fallback to MarkItDown.

MinerU >= 3.4.5 exposes several backends (``mineru/cli/backend_options.py``):

- ``pipeline``: classic layout/MFR/OCR/table pipeline (general purpose).
- ``vlm-engine``: VLM-only parsing via the local MinerU2.5-Pro model
  (high accuracy, needs the VLM model).
- ``hybrid-engine``: pipeline + VLM cross-verification — the upstream default
  and the next-generation high-accuracy solution (needs both model sets).
- ``vlm-http-client`` / ``hybrid-http-client``: same, but talking to a remote
  OpenAI-compatible MinerU server via ``-u/--url``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from model_manager import get_project_root, write_runtime_configs

from .base import (
    BaseConverterEngine,
    ConvertOptions,
    ConvertResult,
    _resolve_output_dir,
    normalize_mineru_backend,
)
from .registry import register_engine

# Windows MAX_PATH workaround: keep temporary stems short.
_MAX_SAFE_STEM_LENGTH = 40

# MinerU CLI can take a while on CPU or with the hybrid "high" effort level.
_DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("DOCS2MD_MINERU_TIMEOUT", "1800"))


def _needs_short_name(file_stem: str) -> bool:
    return len(file_stem) > _MAX_SAFE_STEM_LENGTH


def _sanitize_filename(name: str) -> str:
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    return name


def _find_mineru_bin() -> str | None:
    """Prefer the project venv's mineru binary to avoid ABI mismatches."""
    mineru_name = "mineru.exe" if os.name == "nt" else "mineru"
    project_root = Path(__file__).resolve().parent.parent.parent

    candidate = project_root / "venv" / ("Scripts" if os.name == "nt" else "bin") / mineru_name
    if candidate.is_file():
        return str(candidate)

    candidate = Path(sys.executable).parent / mineru_name
    if candidate.is_file():
        return str(candidate)

    return shutil.which("mineru")


def _find_images_dir(near_dir: Path, search_root: Path) -> Path | None:
    """Locate the images/ directory inside the MinerU output tree."""
    candidate = near_dir / "images"
    if candidate.is_dir():
        return candidate
    for root, dirs, _files in os.walk(search_root):
        if "images" in dirs:
            return Path(root) / "images"
    return None


def _save_markdown(
    text: str,
    output_dir: Path,
    stem: str,
    images_dir: Path | None = None,
) -> str:
    """Write markdown and optionally copy extracted images next to it."""
    out_dir = output_dir / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.md"
    out_path.write_text(text, encoding="utf-8")

    if images_dir is not None and images_dir.is_dir():
        dest_images = out_dir / "images"
        if dest_images.exists():
            shutil.rmtree(dest_images, ignore_errors=True)
            if dest_images.exists():
                import time
                time.sleep(0.3)
                shutil.rmtree(dest_images, ignore_errors=True)
        if not dest_images.exists():
            shutil.copytree(images_dir, dest_images)
            logger.info(f"Copied images to {dest_images}")
        else:
            logger.warning(f"Could not remove existing images directory {dest_images}; skipping image copy")

    return str(out_path.resolve())


def resolve_backend(requested: str) -> str:
    """Resolve the MinerU backend name.

    ``auto`` picks the highest-accuracy backend whose local models are
    available: hybrid-engine (pipeline + VLM) when both model sets are
    downloaded, otherwise the classic pipeline backend.
    """
    from model_manager import pipeline_models_look_complete, vlm_models_present

    requested = normalize_mineru_backend(requested)
    if requested != "auto":
        return requested

    pipeline_ready = pipeline_models_look_complete()
    vlm_ready = vlm_models_present()
    if pipeline_ready and vlm_ready:
        return "hybrid-engine"
    if vlm_ready:
        return "vlm-engine"
    return "pipeline"


def _backend_needs_local_pipeline(backend: str) -> bool:
    return backend in {"pipeline", "hybrid-engine", "hybrid-http-client"}


def _backend_needs_local_vlm(backend: str) -> bool:
    return backend in {"vlm-engine", "hybrid-engine"}


def ensure_backend_models(backend: str) -> None:
    """Raise an actionable error when the requested backend lacks models."""
    from model_manager import pipeline_models_look_complete, vlm_models_present

    if _backend_needs_local_pipeline(backend) and not pipeline_models_look_complete():
        raise RuntimeError(
            "MinerU pipeline models are missing. Download them with "
            "./update.sh (or update.bat on Windows)."
        )
    if _backend_needs_local_vlm(backend) and not vlm_models_present():
        raise RuntimeError(
            "MinerU VLM model (MinerU2.5-Pro-2605-1.2B) is missing but is "
            "required by the selected backend. Download it with "
            "./update.sh auto all (or update.bat auto all)."
        )


def _parse_dir_name(backend: str, method: str) -> str:
    """Return the subdirectory MinerU uses inside its output tree."""
    if backend == "pipeline":
        return method
    if backend.startswith("vlm"):
        return "vlm"
    if backend.startswith("hybrid"):
        return f"hybrid_{method}"
    return method


@register_engine
class MinerUEngine(BaseConverterEngine):
    """Convert PDFs using the MinerU CLI."""

    name = "mineru"
    supported_extensions = frozenset({".pdf"})

    def __init__(self) -> None:
        # Ensure runtime config files point at our local models before any
        # MinerU import or subprocess runs.
        write_runtime_configs()

    def validate_options(self, options: ConvertOptions) -> None:
        """Reject backends whose models (or remote server) are unavailable."""
        if _find_mineru_bin() is None:
            raise ValueError(
                "MinerU CLI not found. Install it with: pip install 'mineru[all]'"
            )
        backend = resolve_backend(options.mineru_backend)
        try:
            ensure_backend_models(backend)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        if backend.endswith("-http-client") and not options.mineru_server_url:
            raise ValueError(
                f"Backend '{backend}' requires a remote MinerU server; pass "
                "server_url in the request."
            )

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        self.validate_options(options)

        mineru_bin = _find_mineru_bin()
        backend = resolve_backend(options.mineru_backend)

        pdf_path = file_path
        original_name = pdf_path.stem
        work_dir = Path(tempfile.mkdtemp(prefix="mineru_"))

        # Windows MAX_PATH workaround for long filenames.
        tmp_pdf: Path | None = None
        if _needs_short_name(original_name):
            short_name = _sanitize_filename(original_name)[:30]
            tmp_pdf = work_dir / f"{short_name}.pdf"
            shutil.copy2(pdf_path, tmp_pdf)
            logger.info(
                f"MinerU: filename '{original_name}' is too long "
                f"({len(original_name)} chars). Using short copy: {tmp_pdf.name}"
            )
            pdf_path = tmp_pdf

        file_name = pdf_path.stem
        mineru_out_dir = work_dir / "output"
        mineru_out_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [
            mineru_bin,
            "-p", str(pdf_path),
            "-o", str(mineru_out_dir),
            "-b", backend,
            "-m", options.mineru_method,
        ]
        if backend.startswith("hybrid-"):
            cmd.extend(["--effort", options.mineru_effort])
        if options.mineru_lang:
            cmd.extend(["-l", options.mineru_lang])
        if not options.mineru_formula_enable:
            cmd.extend(["-f", "false"])
        if not options.mineru_table_enable:
            cmd.extend(["-t", "false"])
        if options.mineru_server_url and backend.endswith("-http-client"):
            cmd.extend(["-u", options.mineru_server_url])
        if options.mineru_start_page:
            cmd.extend(["-s", str(options.mineru_start_page)])
        if options.mineru_end_page is not None:
            cmd.extend(["-e", str(options.mineru_end_page)])

        logger.info(
            f"MinerU CLI ({mineru_bin}): parsing '{file_name}' (original: '{original_name}') "
            f"with backend={backend}, effort={options.mineru_effort}, "
            f"method={options.mineru_method}, formula_enable={options.mineru_formula_enable}, "
            f"table_enable={options.mineru_table_enable}, lang={options.mineru_lang or 'auto'}, "
            f"pages={options.mineru_start_page}:{options.mineru_end_page or 'end'}"
        )

        try:
            env = os.environ.copy()
            env["MINERU_MODEL_SOURCE"] = "local"
            env["MINERU_TOOLS_CONFIG_JSON"] = str(
                get_project_root() / "config" / "mineru.json"
            )
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0:
                stderr_summary = result.stderr.strip()[-500:] if result.stderr else ""
                raise RuntimeError(
                    f"MinerU CLI exited with code {result.returncode}.\nSTDERR: {stderr_summary}"
                )

            md_dir = mineru_out_dir / file_name / _parse_dir_name(backend, options.mineru_method)
            md_file = md_dir / f"{file_name}.md"
            if not md_file.exists():
                for root, _dirs, files in os.walk(mineru_out_dir):
                    for f in files:
                        if f.endswith(".md"):
                            md_file = Path(root) / f
                            break

            if not md_file.exists():
                raise RuntimeError(
                    f"MinerU completed but no .md output file was found in {mineru_out_dir}"
                )

            text = md_file.read_text(encoding="utf-8").strip()
            if tmp_pdf is not None and file_name != original_name:
                text = text.replace(file_name, original_name)

            images_source = _find_images_dir(md_file.parent, mineru_out_dir)

            output_dir = _resolve_output_dir(file_path, options.output_dir)
            out_path = _save_markdown(text, output_dir, original_name, images_source)
            # _save_markdown already returns an absolute, resolved path string.
            images_dest = (
                str((Path(out_path).parent / "images").resolve())
                if images_source is not None
                else None
            )

            return ConvertResult(
                markdown=text,
                engine=self.name,
                output_path=out_path,
                output_dir=str(output_dir.resolve()),
                images_dir=images_dest,
            )

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def health_check(self) -> bool:
        """True if the MinerU binary exists and the local models look complete."""
        from model_manager import pipeline_models_look_complete

        if _find_mineru_bin() is None:
            return False
        return pipeline_models_look_complete()
