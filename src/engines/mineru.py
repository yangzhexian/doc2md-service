"""MinerU converter engine.

Runs the official MinerU CLI for PDF (and image) conversion, with automatic
fallback disabled at this layer — the service handles fallback to MarkItDown.
"""

from __future__ import annotations

import os
import re
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
)
from .registry import register_engine

# Windows MAX_PATH workaround: keep temporary stems short.
_MAX_SAFE_STEM_LENGTH = 40


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


@register_engine
class MinerUEngine(BaseConverterEngine):
    """Convert PDFs (and images) using the MinerU CLI."""

    name = "mineru"
    supported_extensions = frozenset({".pdf"})

    def __init__(self) -> None:
        # Ensure runtime config files point at our local models before any
        # MinerU import or subprocess runs.
        write_runtime_configs()

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        mineru_bin = _find_mineru_bin()
        if mineru_bin is None:
            raise RuntimeError("MinerU CLI not found. Install it with: pip install 'mineru[all]'")

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
            "-b", "pipeline",
            "-m", options.mineru_method,
        ]
        if options.mineru_lang:
            cmd.extend(["-l", options.mineru_lang])
        if not options.mineru_formula_enable:
            cmd.extend(["-f", "false"])
        if not options.mineru_table_enable:
            cmd.extend(["-t", "false"])

        logger.info(
            f"MinerU CLI ({mineru_bin}): parsing '{file_name}' (original: '{original_name}') "
            f"with method={options.mineru_method}, formula_enable={options.mineru_formula_enable}, "
            f"table_enable={options.mineru_table_enable}, lang={options.mineru_lang or 'auto'}"
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
                timeout=600,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode != 0:
                stderr_summary = result.stderr.strip()[-500:] if result.stderr else ""
                raise RuntimeError(
                    f"MinerU CLI exited with code {result.returncode}.\nSTDERR: {stderr_summary}"
                )

            md_dir = mineru_out_dir / file_name / options.mineru_method
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
