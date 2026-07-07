"""Base interfaces and shared helpers for converter engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ConvertOptions:
    """Common options accepted by all engines."""

    output_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # MinerU-specific options
    mineru_method: str = "auto"  # auto, ocr, txt
    mineru_lang: str = ""  # empty means auto-detect
    mineru_formula_enable: bool = True
    mineru_table_enable: bool = True

    @classmethod
    def from_request(
        cls,
        *,
        output_dir: Path | str | None = None,
        engine: str | None = None,
        extra: dict[str, Any] | None = None,
        method: str = "auto",
        lang: str = "",
        formula_enable: bool = True,
        table_enable: bool = True,
    ) -> "ConvertOptions":
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        opts = cls(
            output_dir=output_dir,
            extra=extra or {},
            mineru_method=method,
            mineru_lang=lang,
            mineru_formula_enable=formula_enable,
            mineru_table_enable=table_enable,
        )
        # Allow engine-specific override through extra.
        if extra:
            opts.mineru_method = extra.get("method", opts.mineru_method)
            opts.mineru_lang = extra.get("lang", opts.mineru_lang)
            if "formula_enable" in extra:
                opts.mineru_formula_enable = bool(extra["formula_enable"])
            if "table_enable" in extra:
                opts.mineru_table_enable = bool(extra["table_enable"])
        return opts


@dataclass
class ConvertResult:
    """Result produced by an engine conversion.

    The markdown content is kept for internal fallback / logging purposes; the
    HTTP API does not return it to callers.
    """

    markdown: str
    engine: str
    output_path: str
    output_dir: str = ""
    images_dir: str | None = None
    error: str | None = None
    fallback: bool = False


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
    message: str | None = None


def _resolve_output_dir(input_path: Path, requested: Path | None) -> Path:
    """Resolve the directory where a converted markdown file should be saved."""
    if requested is not None:
        return requested.expanduser().resolve()
    return input_path.parent.expanduser().resolve()


class BaseConverterEngine:
    """Abstract base class for all converter engines."""

    name: str = ""
    supported_extensions: frozenset[str] = frozenset()

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        """Convert a single file to Markdown."""
        raise NotImplementedError

    def health_check(self) -> bool:
        """Return True when the engine can accept conversion requests."""
        raise NotImplementedError

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.supported_extensions
