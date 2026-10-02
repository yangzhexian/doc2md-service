"""API regressions for output collisions, uploads, and fallback semantics."""

from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import converter_service as service  # noqa: E402
from engines.base import OutputWriteError, resolve_output_path, write_text_output  # noqa: E402


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docs2md_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = TestClient(service.app)
        log_patch = patch("converter_service.logger")
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def test_path_conversion_preserves_source_and_supports_reconversion(self) -> None:
        source = self.root / "report.txt"
        source.write_text("first version", encoding="utf-8")
        first = self.client.post("/convert/path", json={"file_path": str(source)})
        self.assertEqual(first.status_code, 200, first.text)
        output = Path(first.json()["output_path"])
        self.assertEqual(output, self.root / "report.txt.docs2md" / "report.md")
        self.assertEqual(source.read_text(encoding="utf-8"), "first version")
        source.write_text("second version", encoding="utf-8")
        second = self.client.post("/convert/path", json={"file_path": str(source)})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["output_path"], str(output))
        self.assertEqual(output.read_text(encoding="utf-8"), "second version")

    def test_folder_preserves_each_same_stem_document(self) -> None:
        csv = self.root / "report.csv"
        txt = self.root / "report.txt"
        csv.write_text("name,value\nalpha,1\n", encoding="utf-8")
        txt.write_text("second document", encoding="utf-8")
        response = self.client.post(
            "/convert/folder",
            json={"folder_path": str(self.root), "engine": "markitdown"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["status"] == "ok" for result in results), results)
        paths = [Path(result["output_path"]) for result in results]
        self.assertEqual(len(set(paths)), 2)
        self.assertIn("alpha", paths[0].read_text(encoding="utf-8"))
        self.assertEqual(paths[1].read_text(encoding="utf-8"), "second document")

    def test_upload_default_output_survives_temporary_input_cleanup(self) -> None:
        output_dir = self.root / "uploads"
        with patch.object(service, "DEFAULT_UPLOAD_OUTPUT_DIR", output_dir):
            response = self.client.post(
                "/convert/upload",
                files={"file": ("note.txt", b"uploaded content", "text/plain")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        path = Path(response.json()["output_path"])
        self.assertEqual(path, output_dir / "note.txt.docs2md" / "note.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "uploaded content")

    def test_pdf_page_failure_does_not_fallback_to_entire_document(self) -> None:
        source = self.root / "paper.pdf"
        source.write_bytes(b"PDF input handled by the mocked MinerU")
        with patch("engines.mineru.MinerUEngine.validate_options"), patch(
            "engines.mineru.MinerUEngine.convert", side_effect=RuntimeError("inference failed")
        ), patch("engines.markitdown.MarkItDownEngine.convert") as fallback:
            response = self.client.post(
                "/convert/path", json={"file_path": str(source), "pages": "1"}
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("cannot preserve", response.json()["detail"])
        self.assertIn("inference failed", response.json()["detail"])
        fallback.assert_not_called()
        self.assertFalse((self.root / "paper.pdf.docs2md").exists())

    def test_whole_document_mineru_failure_still_uses_fallback(self) -> None:
        source = self.root / "note.txt"
        source.write_text("fallback content", encoding="utf-8")
        with patch("engines.mineru.MinerUEngine.validate_options"), patch(
            "engines.mineru.MinerUEngine.convert", side_effect=RuntimeError("inference failed")
        ):
            response = self.client.post(
                "/convert/path", json={"file_path": str(source), "engine": "mineru"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["fallback"])
        self.assertEqual(data["fallback_from"], "mineru")
        self.assertEqual(data["engine"], "markitdown")
        self.assertEqual(Path(data["output_path"]).read_text(encoding="utf-8"), "fallback content")

    def test_explicit_markitdown_rejects_page_selection(self) -> None:
        source = self.root / "paper.pdf"
        source.write_bytes(b"unused PDF")
        response = self.client.post(
            "/convert/path",
            json={"file_path": str(source), "engine": "markitdown", "pages": "1"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("whole documents", response.json()["detail"])

    def test_invalid_requests_report_expected_http_statuses(self) -> None:
        source = self.root / "note.txt"
        source.write_text("note", encoding="utf-8")
        for overrides, expected in (({"tier": "invalid"}, 422), ({"pages": "0"}, 422), ({"engine": "invalid"}, 400)):
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    "/convert/path", json={"file_path": str(source), **overrides}
                )
                self.assertEqual(response.status_code, expected)
        missing = self.client.post("/convert/path", json={"file_path": str(self.root / "missing.txt")})
        self.assertEqual(missing.status_code, 404)

    def test_failed_publication_preserves_previous_markdown(self) -> None:
        output = self.root / "result.md"
        output.write_text("previous result", encoding="utf-8")
        with patch("engines.base.os.replace", side_effect=PermissionError(errno.EACCES, "locked")):
            with self.assertRaises(OutputWriteError):
                write_text_output(output, "replacement result")
        self.assertEqual(output.read_text(encoding="utf-8"), "previous result")
        self.assertEqual(list(self.root.iterdir()), [output])

    def test_write_failure_returns_conflict_without_engine_fallback(self) -> None:
        source = self.root / "note.txt"
        source.write_text("note", encoding="utf-8")
        with patch("engines.base.os.replace", side_effect=PermissionError(errno.EACCES, "locked")):
            response = self.client.post("/convert/path", json={"file_path": str(source)})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Unable to write", response.json()["detail"])

    def test_long_output_directory_names_fit_component_limits_and_remain_distinct(self) -> None:
        for prefix in ("a" * 246, "文" * 81):
            with self.subTest(prefix=prefix):
                first = resolve_output_path(self.root / f"{prefix}1.txt", self.root)[1]
                second = resolve_output_path(self.root / f"{prefix}2.txt", self.root)[1]
                self.assertLessEqual(len(first.parent.name.encode("utf-8")), 240)
                self.assertNotEqual(first.parent, second.parent)
                self.assertEqual(first.name, f"{prefix}1.md")

    def test_valid_long_source_filename_can_publish_output(self) -> None:
        root = self.root
        if os.name == "nt":
            root = Path("\\\\?\\" + str(root))
            self.addCleanup(shutil.rmtree, root)
        source = root / ("a" * 247 + ".txt")
        source.write_text("source", encoding="utf-8")
        _, output = resolve_output_path(source, root)
        write_text_output(output, "converted")
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(output.read_text(encoding="utf-8"), "converted")


if __name__ == "__main__":
    unittest.main()
