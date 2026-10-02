"""MinerU converter engine (MinerU >= 4.0).

Runs ``mineru-kit parse`` which writes either a single Markdown file or a zip
archive (``--format zip``) containing ``markdown.md`` plus an ``images/``
directory. Quality is selected with ``--tier flash|basic|standard|advanced``;
page subsets use the 4.0 page-spec syntax (``all``, ``1-5,8``, ``r3-r1``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from weakref import WeakValueDictionary

from loguru import logger

from .base import (
    BaseConverterEngine,
    ConvertOptions,
    ConvertResult,
    OutputWriteError,
    normalize_mineru_pages,
    normalize_mineru_tier,
    resolve_output_path,
    write_text_output,
)
from .registry import register_engine

# Windows MAX_PATH workaround: keep temporary stems short.
_MAX_SAFE_STEM_LENGTH = 40

# MinerU CLI can take a while on CPU or with higher quality tiers.
_DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("DOCS2MD_MINERU_TIMEOUT", "1800"))
_OUTPUT_READY_GRACE_SECONDS = int(
    os.environ.get("DOCS2MD_MINERU_OUTPUT_READY_GRACE", "20")
)
_OUTPUT_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_OUTPUT_LOCKS_GUARD = threading.Lock()

# PDF / images support every tier; Office / HTML / CSV / EPUB / ... are
# whole-document flash-tier parses (MinerU 4.x, see README tier table).
_NATIVE_FLASH_EXTS = frozenset(
    {
        ".docx",
        ".pptx",
        ".xlsx",
        ".doc",
        ".ppt",
        ".xls",
        ".html",
        ".htm",
        ".csv",
        ".tsv",
        ".rtf",
        ".odt",
        ".ods",
        ".odp",
        ".epub",
        ".ofd",
    }
)
_PDF_AND_IMAGE_EXTS = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
)


def _needs_short_name(file_stem: str) -> bool:
    return len(file_stem) > _MAX_SAFE_STEM_LENGTH


def _sanitize_filename(name: str) -> str:
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    return name


def _find_mineru_kit_bin() -> str | None:
    """Prefer the project venv's mineru-kit binary to avoid ABI mismatches."""
    kit_name = "mineru-kit.exe" if os.name == "nt" else "mineru-kit"
    project_root = Path(__file__).resolve().parent.parent.parent

    candidate = project_root / "venv" / ("Scripts" if os.name == "nt" else "bin") / kit_name
    if candidate.is_file():
        return str(candidate)

    candidate = Path(sys.executable).parent / kit_name
    if candidate.is_file():
        return str(candidate)

    return shutil.which("mineru-kit")


def _effective_tier(file_path: Path, requested: str) -> str:
    """Force flash for formats that only support whole-document parsing."""
    if file_path.suffix.lower() in _NATIVE_FLASH_EXTS:
        return "flash"
    return requested


def ensure_tier_models(tier: str) -> None:
    """Raise an actionable error when the requested tier lacks local models."""
    from model_manager import models_look_complete

    if models_look_complete(tier):
        return
    if tier == "basic":
        raise RuntimeError(
            "MinerU small models are missing. Download them with "
            "./update.sh --tier basic (or update.bat --tier basic)."
        )
    raise RuntimeError(
        f"MinerU models for tier '{tier}' are missing. Download them with "
        f"./update.sh --tier {tier} (or update.bat --tier {tier})."
    )


def _validate_pages(options: ConvertOptions, file_path: Path | None) -> str:
    """Only original PDF inputs support a page subset in MinerU 4.x."""
    pages = normalize_mineru_pages(options.mineru_pages)
    if file_path is not None and file_path.suffix.lower() != ".pdf" and pages != "all":
        raise ValueError(
            "MinerU page selection is only supported for PDF files. "
            f"Use pages='all' for {file_path.suffix.lower() or 'this input'}."
        )
    return pages


def _find_images_dir(near_dir: Path, search_root: Path) -> Path | None:
    """Locate the images/ directory inside the MinerU output tree."""
    candidate = near_dir / "images"
    if candidate.is_dir():
        return candidate
    for root, dirs, _files in os.walk(search_root):
        if "images" in dirs:
            return Path(root) / "images"
    return None


def _find_markdown_file(search_root: Path) -> Path | None:
    """Locate the primary Markdown file produced by mineru-kit."""
    preferred = search_root / "markdown.md"
    if preferred.is_file():
        return preferred
    for root, _dirs, files in os.walk(search_root):
        for name in files:
            if name.endswith(".md"):
                return Path(root) / name
    return None


def _is_zip_file(path: Path) -> bool:
    try:
        # A PK header alone is visible before the archive's central directory
        # has been written, so it must not trigger hung-worker recovery.
        return zipfile.is_zipfile(path)
    except (OSError, ValueError):
        return False


def _locate_zip_output(work_dir: Path, explicit: Path | None = None) -> Path | None:
    """Find the zip archive written by ``mineru-kit parse --format zip``.

    MinerU 4.x writes the zip *at* the ``-o`` path (often named ``result.md``)
    rather than producing a sibling ``*.zip``. Accept either form.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.extend(sorted(work_dir.glob("*.zip")))
    for root, _dirs, files in os.walk(work_dir):
        for name in files:
            if name.endswith(".zip"):
                candidates.append(Path(root) / name)
    for candidate in candidates:
        if candidate.is_file() and _is_zip_file(candidate):
            return candidate
    return None


def _save_markdown(
    text: str,
    output_dir: Path,
    stem: str,
    images_dir: Path | None = None,
    *,
    directory_name: str | None = None,
) -> str:
    """Publish images and Markdown together, restoring old images on failure."""
    out_dir = output_dir / (directory_name or stem)
    out_path = out_dir / f"{stem}.md"
    dest_images = out_dir / "images"
    lock_key = os.path.normcase(str(out_dir.resolve()))
    with _OUTPUT_LOCKS_GUARD:
        output_lock = _OUTPUT_LOCKS.get(lock_key)
        if output_lock is None:
            output_lock = threading.Lock()
            _OUTPUT_LOCKS[lock_key] = output_lock

    with output_lock:
        staging_dir: Path | None = None
        old_images_moved = False
        new_images_published = False
        cleanup_staging = True
        error_path = out_path
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=".docs2md-stage-", dir=out_dir))
            staged_images = staging_dir / "images"
            backup_images = staging_dir / "previous-images"
            error_path = dest_images
            if images_dir is not None:
                shutil.copytree(images_dir, staged_images)

            if dest_images.exists():
                dest_images.rename(backup_images)
                old_images_moved = True
            if images_dir is not None:
                staged_images.rename(dest_images)
                new_images_published = True

            error_path = out_path
            write_text_output(out_path, text)
        except (OSError, OutputWriteError) as exc:
            # The atomic Markdown writer leaves the previous file intact. Undo
            # the image swap so that it still references the previous assets.
            try:
                if new_images_published:
                    dest_images.rename(staging_dir / "unpublished-images")
                if old_images_moved:
                    backup_images.rename(dest_images)
            except OSError as rollback_exc:
                cleanup_staging = False
                recovery_error = OSError(
                    f"{exc}; could not restore previous images: {rollback_exc}. "
                    f"Recovery files are retained in '{staging_dir}'"
                )
                raise OutputWriteError(error_path, recovery_error) from exc
            if isinstance(exc, OutputWriteError):
                raise
            raise OutputWriteError(error_path, exc) from exc
        finally:
            if staging_dir is not None and cleanup_staging:
                try:
                    shutil.rmtree(staging_dir)
                except OSError as exc:
                    # Publication is complete; locked backup files must not
                    # turn a coherent new output into a failed conversion.
                    logger.warning(
                        f"Could not clean output staging directory {staging_dir}: {exc}"
                    )

    return str(out_path.resolve())


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop MinerU and its worker processes when the CLI does not exit cleanly."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.kill()


def _read_log_tail(path: Path, limit: int = 500) -> str:
    """Return only the tail of a (possibly huge) MinerU log file."""
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit * 4))
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return text[-limit:] if len(text) > limit else text


def _locate_primary_output(work_dir: Path, explicit: Path | None = None) -> Path | None:
    """Find a complete zip, excluding MinerU's intermediate Markdown files."""
    return _locate_zip_output(work_dir, explicit=explicit)


def _run_mineru_process(
    cmd: list[str],
    *,
    env: dict[str, str],
    output_dir: Path,
    explicit_output: Path | None = None,
    log_path: Path,
) -> tuple[int, str, bool]:
    """Run MinerU, accepting stable output when its Windows workers never exit.

    ``output_dir`` / ``explicit_output`` are scanned for a complete zip whose
    mtime is used to detect "output ready but process hung".
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    returncode: int | None = None
    recovered_from_hang = False
    timeout_without_output = False

    with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
        process = subprocess.Popen(
            cmd,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + _DEFAULT_TIMEOUT_SECONDS
        output_ready_since: float | None = None
        output_mtime: float | None = None

        while True:
            returncode = process.poll()
            if returncode is not None:
                break

            expected_output = _locate_primary_output(output_dir, explicit=explicit_output)
            if expected_output is not None and expected_output.is_file():
                current_mtime = expected_output.stat().st_mtime
                if output_mtime != current_mtime:
                    output_mtime = current_mtime
                    output_ready_since = time.monotonic()
                elif (
                    output_ready_since is not None
                    and time.monotonic() - output_ready_since >= _OUTPUT_READY_GRACE_SECONDS
                ):
                    logger.warning(
                        "MinerU produced a stable output file but did not exit; "
                        "terminating its worker tree and using the generated output."
                    )
                    _terminate_process_tree(process)
                    recovered_from_hang = True
                    break

            if time.monotonic() >= deadline:
                _terminate_process_tree(process)
                if _locate_primary_output(output_dir, explicit=explicit_output) is not None:
                    logger.warning(
                        "MinerU reached its timeout after producing output; "
                        "using the generated output."
                    )
                    recovered_from_hang = True
                else:
                    timeout_without_output = True
                break

            time.sleep(1)

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            process.wait(timeout=10)

        log_stream.flush()

    stderr = _read_log_tail(log_path)
    if timeout_without_output:
        raise TimeoutError(
            f"MinerU did not finish within {_DEFAULT_TIMEOUT_SECONDS} seconds"
        )
    returncode_out = 0 if recovered_from_hang or returncode is None else returncode
    return returncode_out, stderr, recovered_from_hang


@register_engine
class MinerUEngine(BaseConverterEngine):
    """Convert documents using the MinerU 4.x ``mineru-kit`` CLI."""

    name = "mineru"
    supported_extensions = frozenset(_PDF_AND_IMAGE_EXTS | _NATIVE_FLASH_EXTS)

    def validate_options(self, options: ConvertOptions, file_path: Path | None = None) -> None:
        """Reject tiers whose models (or remote mode) are unavailable."""
        _validate_pages(options, file_path)
        if _find_mineru_kit_bin() is None:
            raise ValueError(
                "MinerU CLI (mineru-kit) not found. Install it with: "
                "pip install 'mineru>=4.0,<5'"
            )
        tier = normalize_mineru_tier(options.mineru_tier)
        if file_path is not None:
            tier = _effective_tier(file_path, tier)
        if options.mineru_remote:
            return
        try:
            ensure_tier_models(tier)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        pages = _validate_pages(options, file_path)
        mineru_bin = _find_mineru_kit_bin()
        if mineru_bin is None:
            raise ValueError(
                "MinerU CLI (mineru-kit) not found. Install it with: "
                "pip install 'mineru>=4.0,<5'"
            )

        requested_tier = normalize_mineru_tier(options.mineru_tier)
        tier = _effective_tier(file_path, requested_tier)
        if not options.mineru_remote:
            ensure_tier_models(tier)

        original_name = file_path.stem
        work_dir = Path(tempfile.mkdtemp(prefix="mineru_"))

        # Windows MAX_PATH workaround for long filenames.
        src_path = file_path
        tmp_src: Path | None = None
        # HTML resolves local images and stylesheets relative to its source.
        # Keep that source in place instead of moving only its main file.
        if (
            _needs_short_name(original_name)
            and file_path.suffix.lower() not in {".html", ".htm"}
        ):
            short_name = _sanitize_filename(original_name)[:30]
            tmp_src = work_dir / f"{short_name}{file_path.suffix.lower()}"
            shutil.copy2(file_path, tmp_src)
            logger.info(
                f"MinerU: filename '{original_name}' is too long "
                f"({len(original_name)} chars). Using short copy: {tmp_src.name}"
            )
            src_path = tmp_src

        file_name = src_path.stem
        out_md_path = work_dir / "result.md"

        cmd: list[str] = [
            mineru_bin,
            "parse",
            str(src_path),
            "-o", str(out_md_path),
            "--tier", tier,
            "--format", "zip",
        ]
        if file_path.suffix.lower() == ".pdf":
            cmd.extend(["--pages", pages])
        if options.mineru_remote:
            cmd.append("--remote")

        logger.info(
            f"MinerU CLI ({mineru_bin}): parsing '{file_name}' (original: '{original_name}') "
            f"with tier={tier}, pages={options.mineru_pages}, remote={options.mineru_remote}"
        )

        try:
            env = os.environ.copy()
            env.setdefault("MINERU_MODEL_SOURCE", "local")
            env["PYTHONIOENCODING"] = "utf-8"

            returncode, stderr, recovered_from_hang = _run_mineru_process(
                cmd,
                env=env,
                output_dir=work_dir,
                explicit_output=out_md_path,
                log_path=work_dir / "mineru.log",
            )
            if returncode != 0:
                raise RuntimeError(
                    f"MinerU CLI exited with code {returncode}.\nSTDERR: {stderr}"
                )
            if recovered_from_hang:
                logger.info("MinerU output recovered after CLI worker shutdown.")

            extract_dir = work_dir / "extracted"
            zip_path = _locate_zip_output(work_dir, explicit=out_md_path)
            md_file: Path | None = None
            images_source: Path | None = None

            if zip_path is not None:
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)
                md_file = _find_markdown_file(extract_dir)
                if md_file is not None:
                    images_source = _find_images_dir(md_file.parent, extract_dir)
                else:
                    images_source = _find_images_dir(extract_dir, extract_dir)
            else:
                # Plain markdown output (no images requested / simple inputs).
                md_file = _find_markdown_file(work_dir)
                if md_file is not None:
                    images_source = _find_images_dir(md_file.parent, work_dir)

            if md_file is None or not md_file.is_file():
                raise RuntimeError(
                    f"MinerU completed but no markdown output was found in {work_dir}"
                )

            text = md_file.read_text(encoding="utf-8").strip()
            # MinerU 4.x asset names are independent of the input stem. Keep
            # exported destinations intact, including real external links.
            output_dir, output_path = resolve_output_path(file_path, options.output_dir)
            out_path = _save_markdown(
                text,
                output_dir,
                original_name,
                images_source,
                directory_name=output_path.parent.name,
            )
            images_dest = (
                str((Path(out_path).parent / "images").resolve())
                if images_source is not None
                else None
            )

            return ConvertResult(
                markdown="",
                engine=self.name,
                output_path=out_path,
                output_dir=str(output_dir.resolve()),
                images_dir=images_dest,
            )

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
