"""Base interfaces and shared helpers for converter engines."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

# MinerU >= 4.0 public quality tiers (mineru-kit parse --tier).
MINERU_TIERS: frozenset[str] = frozenset({"flash", "basic", "standard", "advanced"})

_PAGE_RANGE_RE = re.compile(r"^(r?[1-9][0-9]*)(?:\s*-\s*(r?[1-9][0-9]*))?$")


class OutputWriteError(RuntimeError):
    """Raised when an engine cannot persist the converted Markdown output."""

    def __init__(self, output_path: Path, cause: OSError) -> None:
        reason = cause.strerror or str(cause)
        super().__init__(
            f"Unable to write conversion output '{output_path}': {reason}. "
            "Choose a writable output_dir and close any program that may be "
            "locking the existing Markdown file."
        )
        self.output_path = output_path
        self.cause = cause


def write_text_output(output_path: Path, text: str) -> None:
    """Publish Markdown atomically, preserving the previous file on failure."""
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".docs2md_", suffix=".tmp", dir=output_path.parent
        )
        temporary_path = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary_path, output_path)
    except OSError as exc:
        raise OutputWriteError(output_path, exc) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def normalize_mineru_tier(tier: str) -> str:
    """Normalize a MinerU 4.x quality tier name."""
    tier = (tier or "standard").strip().lower()
    if tier in MINERU_TIERS:
        return tier
    raise ValueError(
        f"Unsupported MinerU tier '{tier}'. Allowed values: "
        + ", ".join(sorted(MINERU_TIERS))
    )


def normalize_mineru_pages(pages: str) -> str:
    """Normalize a MinerU 4.x page-spec string.

    Accepts ``all`` (default) or comma-separated 1-based ranges such as
    ``1-5``, ``1-5,8``, ``r3-r1`` (``rN`` counts from the end).
    """
    pages = (pages or "all").strip().lower()
    if pages == "all":
        return "all"
    parts = [p.strip() for p in pages.split(",")]
    normalized: list[str] = []
    for part in parts:
        match = _PAGE_RANGE_RE.fullmatch(part)
        if match is None:
            raise ValueError(
                f"Invalid MinerU pages spec '{pages}'. Use 'all' or comma-separated "
                "ranges like '1-5,8,r3-r1' (1-based; rN = Nth from the end)."
            )
        start, end = match.group(1), match.group(2)
        if end is not None:
            start_index = -int(start[1:]) if start.startswith("r") else int(start)
            end_index = -int(end[1:]) if end.startswith("r") else int(end)
            if (start_index > 0) == (end_index > 0) and start_index > end_index:
                raise ValueError(f"Invalid MinerU pages spec '{pages}': reversed range")
        normalized.append(start if end is None else f"{start}-{end}")
    return ",".join(normalized)


@dataclass
class ConvertOptions:
    """Common options accepted by all engines."""

    output_dir: Path | None = None

    # MinerU >= 4.0 options
    mineru_tier: str = "standard"  # flash, basic, standard, advanced
    mineru_remote: bool = False  # --remote
    mineru_pages: str = "all"  # "all" | "1-5,8" | "r3-r1"

    @classmethod
    def from_request(
        cls,
        *,
        output_dir: Path | str | None = None,
        tier: str = "standard",
        remote: bool = False,
        pages: str = "all",
    ) -> "ConvertOptions":
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        return cls(
            output_dir=output_dir,
            mineru_tier=normalize_mineru_tier(tier),
            mineru_remote=bool(remote),
            mineru_pages=normalize_mineru_pages(pages),
        )


@dataclass
class ConvertResult:
    """Result produced by an engine conversion.

    ``markdown`` is cleared after the output is persisted so long documents
    are not held in memory; the HTTP API never returns the body either.
    """

    markdown: str
    engine: str
    output_path: str
    output_dir: str = ""
    images_dir: str | None = None
    error: str | None = None
    fallback: bool = False
    fallback_from: str | None = None


@dataclass
class ConvertStatusResponse:
    """Lightweight status returned by the HTTP API.

    Never includes the full markdown content.
    """

    success: bool
    engine: str
    output_path: str
    output_dir: str
    images_dir: str | None
    fallback: bool
    fallback_from: str | None = None
    message: str | None = None


def _resolve_output_dir(input_path: Path, requested: Path | None) -> Path:
    """Resolve the directory where a converted markdown file should be saved."""
    if requested is not None:
        return requested.expanduser().resolve()
    return input_path.parent.expanduser().resolve()


def resolve_output_path(input_path: Path, requested: Path | None) -> tuple[Path, Path]:
    """Keep different source extensions separate, even beside the input file."""
    output_dir = _resolve_output_dir(input_path, requested)
    directory_name = f"{input_path.name}.docs2md"
    # Bound the component in UTF-8 as well as Windows characters; a digest
    # distinguishes long names that share the same truncated prefix.
    if len(directory_name.encode("utf-8")) > 240:
        digest = sha256(input_path.name.encode("utf-8")).hexdigest()[:16]
        suffix = f"-{digest}.docs2md"
        available = 240 - len(suffix.encode("utf-8"))
        prefix = input_path.name.encode("utf-8")[:available].decode("utf-8", errors="ignore")
        directory_name = prefix + suffix
    document_dir = output_dir / directory_name
    return output_dir, document_dir / f"{input_path.stem}.md"


class BaseConverterEngine:
    """Abstract base class for all converter engines."""

    name: str = ""
    supported_extensions: frozenset[str] = frozenset()

    def validate_options(self, options: ConvertOptions, file_path: Path | None = None) -> None:
        """Validate request options before conversion.

        Engines should raise ValueError with an actionable message for
        configuration problems (missing models, missing binary, ...).
        The service maps these to HTTP 400 without engine fallback.
        """

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        """Convert a single file to Markdown."""
        raise NotImplementedError

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.supported_extensions
