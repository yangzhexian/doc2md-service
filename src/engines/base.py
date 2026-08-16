"""Base interfaces and shared helpers for converter engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# MinerU >= 3.4.5 public backend choices (mineru/cli/backend_options.py).
MINERU_BACKENDS: frozenset[str] = frozenset(
    {
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    }
)
MINERU_EFFORTS: frozenset[str] = frozenset({"medium", "high"})

# Canonical OCR languages accepted by MinerU >= 3.4.5 (mineru/utils/ocr_language.py).
MINERU_OCR_LANGS: frozenset[str] = frozenset(
    {
        "ch",
        "ch_server",
        "korean",
        "ta",
        "te",
        "ka",
        "th",
        "el",
        "arabic",
        "east_slavic",
        "cyrillic",
        "devanagari",
    }
)

# Legacy language hints accepted by the CLI and aliased to canonical keys.
_MINERU_LANG_ALIASES: dict[str, str] = {}
for _lang in ("en", "japan", "chinese_cht", "latin"):
    _MINERU_LANG_ALIASES[_lang] = "ch"
for _lang in ("ru", "be", "uk"):
    _MINERU_LANG_ALIASES[_lang] = "east_slavic"
for _lang in ("ar", "fa", "ug", "ur", "ps", "ku", "sd", "bal"):
    _MINERU_LANG_ALIASES[_lang] = "arabic"
for _lang in (
    "rs_cyrillic", "bg", "mn", "abq", "ady", "kbd", "ava", "dar", "inh", "che",
    "lbe", "lez", "tab", "kk", "ky", "tg", "mk", "tt", "cv", "ba", "mhr", "mo",
    "udm", "kv", "os", "bua", "xal", "tyv", "sah", "kaa",
):
    _MINERU_LANG_ALIASES[_lang] = "cyrillic"
for _lang in ("hi", "mr", "ne", "bh", "mai", "ang", "bho", "mah", "sck", "new", "gom", "sa", "bgc"):
    _MINERU_LANG_ALIASES[_lang] = "devanagari"


def normalize_mineru_lang(lang: str) -> str:
    """Normalize a MinerU OCR language hint to a canonical key.

    Returns "" (auto-detect) for empty input and raises ValueError for
    languages MinerU >= 3.4.5 no longer accepts.
    """
    lang = (lang or "").strip().lower()
    if not lang:
        return ""
    if lang in MINERU_OCR_LANGS:
        return lang
    if lang in _MINERU_LANG_ALIASES:
        return _MINERU_LANG_ALIASES[lang]
    raise ValueError(
        f"Unsupported MinerU language '{lang}'. Allowed values: "
        + ", ".join(sorted(MINERU_OCR_LANGS))
        + " (plus common aliases such as 'en', 'ru', 'ar', 'hi')"
    )


def normalize_mineru_backend(backend: str) -> str:
    """Normalize a MinerU backend name; 'auto' passes through for resolution."""
    backend = (backend or "auto").strip().lower()
    if backend == "auto" or backend in MINERU_BACKENDS:
        return backend
    raise ValueError(
        f"Unsupported MinerU backend '{backend}'. Allowed values: auto, "
        + ", ".join(sorted(MINERU_BACKENDS))
    )


def normalize_mineru_effort(effort: str) -> str:
    """Normalize the hybrid backend effort level."""
    effort = (effort or "medium").strip().lower()
    if effort in MINERU_EFFORTS:
        return effort
    raise ValueError(
        f"Unsupported MinerU effort '{effort}'. Allowed values: medium, high"
    )


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
    mineru_backend: str = "auto"  # auto, pipeline, vlm-engine, hybrid-engine, ...
    mineru_effort: str = "medium"  # hybrid backend only: medium, high
    mineru_server_url: str = ""  # required for *-http-client backends
    mineru_start_page: int = 0  # 0-based first page to parse
    mineru_end_page: int | None = None  # 0-based inclusive last page

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
        backend: str = "auto",
        effort: str = "medium",
        server_url: str | None = None,
        start_page: int = 0,
        end_page: int | None = None,
    ) -> "ConvertOptions":
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        opts = cls(
            output_dir=output_dir,
            extra=extra or {},
            mineru_method=method,
            mineru_lang=normalize_mineru_lang(lang),
            mineru_formula_enable=formula_enable,
            mineru_table_enable=table_enable,
            mineru_backend=normalize_mineru_backend(backend),
            mineru_effort=normalize_mineru_effort(effort),
            mineru_server_url=server_url or "",
            mineru_start_page=start_page,
            mineru_end_page=end_page,
        )
        # Allow engine-specific override through extra.
        if extra:
            opts.mineru_method = extra.get("method", opts.mineru_method)
            opts.mineru_lang = normalize_mineru_lang(extra.get("lang", opts.mineru_lang))
            if "formula_enable" in extra:
                opts.mineru_formula_enable = bool(extra["formula_enable"])
            if "table_enable" in extra:
                opts.mineru_table_enable = bool(extra["table_enable"])
            opts.mineru_backend = normalize_mineru_backend(extra.get("backend", opts.mineru_backend))
            opts.mineru_effort = normalize_mineru_effort(extra.get("effort", opts.mineru_effort))
            opts.mineru_server_url = extra.get("server_url", opts.mineru_server_url) or ""
            if "start_page" in extra:
                opts.mineru_start_page = int(extra["start_page"])
            if "end_page" in extra:
                opts.mineru_end_page = int(extra["end_page"])
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

    def validate_options(self, options: ConvertOptions) -> None:
        """Validate request options before conversion.

        Engines should raise ValueError with an actionable message for
        configuration problems (missing models, missing server URL, ...).
        The service maps these to HTTP 400 without engine fallback.
        """

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        """Convert a single file to Markdown."""
        raise NotImplementedError

    def health_check(self) -> bool:
        """Return True when the engine can accept conversion requests."""
        raise NotImplementedError

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.supported_extensions
