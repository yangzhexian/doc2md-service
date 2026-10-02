"""Regression coverage for MinerU options and coherent output publication."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engines.base import ConvertOptions, OutputWriteError  # noqa: E402
from engines.mineru import MinerUEngine, _save_markdown  # noqa: E402


class MinerUOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = MinerUEngine()
        find_binary = patch(
            "engines.mineru._find_mineru_kit_bin", return_value="mineru-kit"
        )
        find_binary.start()
        self.addCleanup(find_binary.stop)
        ensure_models = patch("engines.mineru.ensure_tier_models")
        ensure_models.start()
        self.addCleanup(ensure_models.stop)

    @staticmethod
    def _write_zip(cmd: list[str], **kwargs) -> tuple[int, str, bool]:
        with zipfile.ZipFile(kwargs["explicit_output"], "w") as archive:
            archive.writestr("markdown.md", f"converted {Path(cmd[2]).suffix}")
        return 0, "", False

    def _images(self, name: str, filename: str) -> Path:
        directory = self.root / name
        directory.mkdir()
        (directory / filename).write_bytes(filename.encode())
        return directory

    def _existing_output(self) -> tuple[Path, Path]:
        old_images = self._images("old-source", "old.png")
        path = Path(
            _save_markdown("![old](images/old.png)", self.root / "out", "doc", old_images)
        )
        (path.parent / "notes.txt").write_text("user notes", encoding="utf-8")
        return path, path.parent / "images"

    def _assert_old_output(self, markdown: Path, images: Path) -> None:
        self.assertEqual(markdown.read_text(encoding="utf-8"), "![old](images/old.png)")
        self.assertEqual({p.name for p in images.iterdir()}, {"old.png"})
        self.assertEqual((markdown.parent / "notes.txt").read_text(), "user notes")

    def test_non_pdf_inputs_do_not_pass_default_pages_to_cli(self) -> None:
        for extension in (".html", ".csv", ".docx", ".odt", ".png", ".jpg"):
            with self.subTest(extension=extension):
                source = self.root / f"input{extension}"
                source.write_bytes(b"source remains intact")
                with patch(
                    "engines.mineru._run_mineru_process", side_effect=self._write_zip
                ) as run:
                    result = self.engine.convert(source, ConvertOptions())
                self.assertNotIn("--pages", run.call_args.args[0])
                self.assertEqual(
                    Path(result.output_path),
                    self.root / f"input{extension}.docs2md" / "input.md",
                )
                self.assertEqual(source.read_bytes(), b"source remains intact")

    def test_pdf_page_subset_is_forwarded(self) -> None:
        source = self.root / "paper.pdf"
        source.write_bytes(b"PDF input")
        with patch(
            "engines.mineru._run_mineru_process", side_effect=self._write_zip
        ) as run:
            self.engine.convert(source, ConvertOptions(mineru_pages="2-3"))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--pages") + 1], "2-3")

    def test_non_pdf_page_subset_is_rejected_before_conversion(self) -> None:
        for extension in (".html", ".csv", ".docx", ".png"):
            with self.subTest(extension=extension), patch(
                "engines.mineru._run_mineru_process"
            ) as run:
                source = self.root / f"input{extension}"
                options = ConvertOptions(mineru_pages="2")
                with self.assertRaisesRegex(ValueError, "only supported for PDF"):
                    self.engine.validate_options(options, source)
                with self.assertRaisesRegex(ValueError, "only supported for PDF"):
                    self.engine.convert(source, options)
                run.assert_not_called()

    def test_long_html_keeps_original_location_and_links(self) -> None:
        stem = "article-about-concurrency-and-correctness-in-python"
        source = self.root / f"{stem}.html"
        source.write_text("<img src='figure.png'>", encoding="utf-8")
        original_link = f"[source](https://example.com/{stem[:30]})"

        def write_html_zip(cmd: list[str], **kwargs) -> tuple[int, str, bool]:
            self.assertEqual(Path(cmd[2]), source)
            with zipfile.ZipFile(kwargs["explicit_output"], "w") as archive:
                archive.writestr("markdown.md", original_link)
            return 0, "", False

        with patch("engines.mineru._run_mineru_process", side_effect=write_html_zip):
            result = self.engine.convert(source, ConvertOptions())
        self.assertEqual(Path(result.output_path).read_text(encoding="utf-8"), original_link)

    def test_short_temporary_pdf_name_does_not_rewrite_external_links(self) -> None:
        stem = "article-about-concurrency-and-correctness-in-python"
        source = self.root / f"{stem}.pdf"
        source.write_bytes(b"PDF input")
        original_link = f"[source](https://example.com/{stem[:30]})"

        def write_pdf_zip(cmd: list[str], **kwargs) -> tuple[int, str, bool]:
            self.assertNotEqual(Path(cmd[2]), source)
            self.assertEqual(Path(cmd[2]).stem, stem[:30])
            with zipfile.ZipFile(kwargs["explicit_output"], "w") as archive:
                archive.writestr("markdown.md", original_link)
            return 0, "", False

        with patch("engines.mineru._run_mineru_process", side_effect=write_pdf_zip):
            result = self.engine.convert(source, ConvertOptions())
        self.assertEqual(Path(result.output_path).read_text(encoding="utf-8"), original_link)

    def test_image_copy_failure_keeps_previous_output(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        with patch("engines.mineru.shutil.copytree", side_effect=PermissionError("copy locked")):
            with self.assertRaises(OutputWriteError):
                _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self._assert_old_output(markdown, images)
        self.assertFalse(list(markdown.parent.glob(".docs2md-stage-*")))

    def test_locked_old_images_keep_previous_output(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        original_rename = Path.rename

        def locked_rename(path: Path, target: Path) -> Path:
            if path == images:
                raise PermissionError("old images locked")
            return original_rename(path, target)

        with patch.object(Path, "rename", locked_rename):
            with self.assertRaises(OutputWriteError):
                _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self._assert_old_output(markdown, images)

    def test_image_publish_failure_restores_previous_output(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        original_rename = Path.rename

        def fail_publish(path: Path, target: Path) -> Path:
            if path.name == "images" and path.parent.name.startswith(".docs2md-stage-"):
                raise PermissionError("new images publish locked")
            return original_rename(path, target)

        with patch.object(Path, "rename", fail_publish):
            with self.assertRaises(OutputWriteError):
                _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self._assert_old_output(markdown, images)

    def test_markdown_replace_failure_restores_previous_images(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        with patch("engines.base.os.replace", side_effect=PermissionError("Markdown locked")):
            with self.assertRaises(OutputWriteError):
                _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self._assert_old_output(markdown, images)

    def test_rollback_failure_retains_recoverable_previous_images(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        original_rename = Path.rename

        def fail_rollback(path: Path, target: Path) -> Path:
            if path == images and target.name == "unpublished-images":
                raise PermissionError("new images locked during rollback")
            return original_rename(path, target)

        with patch.object(Path, "rename", fail_rollback), patch(
            "engines.base.os.replace", side_effect=PermissionError("Markdown locked")
        ):
            with self.assertRaisesRegex(OutputWriteError, "Recovery files are retained"):
                _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self.assertEqual(markdown.read_text(encoding="utf-8"), "![old](images/old.png)")
        staging_dirs = list(markdown.parent.glob(".docs2md-stage-*"))
        self.assertEqual(len(staging_dirs), 1)
        self.assertTrue((staging_dirs[0] / "previous-images" / "old.png").is_file())

    def test_successful_replacement_preserves_other_files_and_removes_stale_images(self) -> None:
        markdown, images = self._existing_output()
        new_images = self._images("new-source", "new.png")
        _save_markdown("![new](images/new.png)", self.root / "out", "doc", new_images)
        self.assertEqual(markdown.read_text(encoding="utf-8"), "![new](images/new.png)")
        self.assertEqual({p.name for p in images.iterdir()}, {"new.png"})
        self.assertEqual((markdown.parent / "notes.txt").read_text(), "user notes")
        _save_markdown("text only", self.root / "out", "doc")
        self.assertEqual(markdown.read_text(encoding="utf-8"), "text only")
        self.assertFalse(images.exists())
        self.assertFalse(list(markdown.parent.glob(".docs2md-stage-*")))


if __name__ == "__main__":
    unittest.main()
