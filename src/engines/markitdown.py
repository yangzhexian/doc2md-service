"""MarkItDown fallback converter engine."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from markitdown import MarkItDown

from .base import (
    BaseConverterEngine,
    ConvertOptions,
    ConvertResult,
    _resolve_output_dir,
)
from .registry import register_engine


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
            ".html",
            ".htm",
            ".csv",
            ".txt",
            ".json",
            ".xml",
            ".epub",
            ".rtf",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
        }
    )

    def __init__(self) -> None:
        self._converter = MarkItDown()

    def convert(self, file_path: Path, options: ConvertOptions) -> ConvertResult:
        logger.info(f"MarkItDown: converting '{file_path}'")
        try:
            md_result = self._converter.convert(str(file_path))
        except Exception as exc:
            logger.exception("MarkItDown conversion failed")
            return ConvertResult(
                markdown="",
                engine=self.name,
                output_path="",
                error=f"MarkItDown failed: {exc}",
            )

        output_dir = _resolve_output_dir(file_path, options.output_dir)
        out_dir = output_dir / file_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{file_path.stem}.md"
        out_path.write_text(md_result.text_content, encoding="utf-8")
        out_path_resolved = str(out_path.resolve())

        return ConvertResult(
            markdown=md_result.text_content,
            engine=self.name,
            output_path=out_path_resolved,
            output_dir=str(output_dir.resolve()),
            images_dir=None,
        )

    def health_check(self) -> bool:
        try:
            MarkItDown()
            return True
        except Exception:
            return False
