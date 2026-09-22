"""MarkItDown fallback converter engine."""

from __future__ import annotations

import threading
from pathlib import Path

from loguru import logger
from markitdown import MarkItDown

from .base import (
    BaseConverterEngine,
    ConvertOptions,
    ConvertResult,
    OutputWriteError,
    _resolve_output_dir,
    write_text_output,
)
from .registry import register_engine

# MarkItDown.__init__ builds a requests.Session and a magika.Magika() instance;
# reuse one converter across requests instead of paying that cost every time.
_MARKITDOWN: MarkItDown | None = None
_MARKITDOWN_LOCK = threading.Lock()


def _get_markitdown() -> MarkItDown:
    global _MARKITDOWN
    with _MARKITDOWN_LOCK:
        if _MARKITDOWN is None:
            _MARKITDOWN = MarkItDown()
        return _MARKITDOWN


@register_engine
class MarkItDownEngine(BaseConverterEngine):
    """Convert a wide range of documents using Microsoft's MarkItDown."""

    name = "markitdown"
    supported_extensions = frozenset(
        {
            ".pdf",
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
            ".txt",
            ".json",
            ".xml",
            ".epub",
            ".rtf",
            ".odt",
            ".ods",
            ".odp",
            ".ofd",
            ".ipynb",
            ".msg",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
        }
    )

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        logger.info(f"MarkItDown: converting '{file_path}'")
        try:
            md_result = _get_markitdown().convert(str(file_path))
        except Exception as exc:
            logger.exception("MarkItDown conversion failed")
            return ConvertResult(
                markdown="",
                engine=self.name,
                output_path="",
                error=f"MarkItDown failed: {exc}",
            )

        # 0.1.8 canonical attribute (``text_content`` is a soft-deprecated alias).
        text = md_result.markdown

        output_dir = _resolve_output_dir(file_path, options.output_dir)
        out_dir = output_dir / file_path.stem
        out_path = out_dir / f"{file_path.stem}.md"
        # Re-raise so the service can map write failures to HTTP 409.
        write_text_output(out_path, text)
        out_path_resolved = str(out_path.resolve())

        return ConvertResult(
            markdown="",
            engine=self.name,
            output_path=out_path_resolved,
            output_dir=str(output_dir.resolve()),
            images_dir=None,
        )
