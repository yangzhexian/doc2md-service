"""Regression tests for the MinerU 4.x integration."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model_manager  # noqa: E402
from engines.base import ConvertOptions, normalize_mineru_pages  # noqa: E402
from engines.mineru import MinerUEngine, _locate_primary_output  # noqa: E402


class MinerU4Tests(unittest.TestCase):
    def test_page_spec_rejects_invalid_ranges_before_conversion(self) -> None:
        for spec in ("0", "r0", "1-5,", "1,,3", "3-1", "r1-r3", "01"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                normalize_mineru_pages(spec)
        self.assertEqual(normalize_mineru_pages("1 - 3, r3-r1"), "1-3,r3-r1")

    def test_windows_config_selects_llama_cpp(self) -> None:
        with patch.dict(os.environ, {"DOCS2MD_MINERU_VLM_ENGINE": ""}), patch.object(
            model_manager.sys, "platform", "win32"
        ):
            self.assertIn("engine: llama-cpp", model_manager.build_mineru_config_text())

    def test_llama_cpp_requires_model_and_projector(self) -> None:
        from mineru.model.registry import MINERU_2_5_PRO_2605_1_2B_GGUF as repo

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gguf_dir = root / repo.name
            gguf_dir.mkdir()
            with patch.dict(os.environ, {
                "MINERU_MODEL_BASE_DIR": str(root), "MINERU_MODEL_VLM_ENGINE": "llama-cpp",
            }):
                (gguf_dir / ".mineru_complete").touch()
                (gguf_dir / repo.paths["mmproj"]).write_bytes(b"projector")
                self.assertFalse(model_manager.vlm_models_present())
                (gguf_dir / repo.paths["main"]).write_bytes(b"weights")
                self.assertTrue(model_manager.vlm_models_present())

    def test_marker_without_weights_is_not_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir)
            (package / ".mineru_complete").touch()
            self.assertFalse(model_manager._package_present(package))

    def test_remote_and_native_flash_do_not_require_standard_models(self) -> None:
        engine = MinerUEngine()
        with patch("engines.mineru._find_mineru_kit_bin", return_value="mineru-kit"), patch(
            "engines.mineru.ensure_tier_models"
        ) as ensure_models:
            engine.validate_options(ConvertOptions(mineru_tier="standard"), Path("input.docx"))
            ensure_models.assert_called_once_with("flash")
            ensure_models.reset_mock()
            engine.validate_options(
                ConvertOptions(mineru_tier="standard", mineru_remote=True), Path("input.pdf")
            )
            ensure_models.assert_not_called()

    def test_incomplete_zip_and_intermediate_markdown_are_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "result.md"
            (root / ".result").mkdir()
            (root / ".result" / "markdown.md").write_text("intermediate")
            output.write_bytes(b"PK\x03\x04")
            self.assertIsNone(_locate_primary_output(root, output))
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("markdown.md", "complete")
            self.assertEqual(_locate_primary_output(root, output), output)


if __name__ == "__main__":
    unittest.main()
