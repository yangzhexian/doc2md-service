"""API resource errors and actual isolated conversions through every endpoint."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from fastapi.testclient import TestClient  # noqa: E402
import converter_service as service  # noqa: E402
from resource_control import (  # noqa: E402
    ConversionTimeoutError, ResourceBusyError, ResourceConfig, ResourceController,
)
from resource_limits import ResourceLimits  # noqa: E402
from test_real_documents import isolated_environment  # noqa: E402


class ResourceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docs2md_resource_api_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, isolated_environment(self.root), clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.root / "note.txt"
        self.source.write_text("Resource-controlled conversion", encoding="utf-8")
        self.client = TestClient(service.app)
        controller = patch.object(
            service, "RESOURCE_CONTROLLER", ResourceController(ResourceConfig(conversion_timeout_seconds=60))
        )
        controller.start()
        self.addCleanup(controller.stop)

    def test_actual_worker_path_publishes_final_output_and_releases_capacity(self) -> None:
        response = self.client.post("/convert/path", json={"file_path": str(self.source)})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        output = self.root / "note.txt.docs2md" / "note.md"
        self.assertEqual(Path(data["output_path"]), output)
        self.assertEqual(output.read_text(encoding="utf-8"), self.source.read_text(encoding="utf-8"))
        self.assertEqual(data["message"], f"Saved to {output}")
        snapshot = service.RESOURCE_CONTROLLER.snapshot()
        self.assertEqual((snapshot["active"], snapshot["pending"], snapshot["completed"]), (0, 0, 1))

    def test_actual_worker_upload_and_folder_use_persistent_destinations(self) -> None:
        destination = self.root / "uploads"
        with patch.object(service, "DEFAULT_UPLOAD_OUTPUT_DIR", destination):
            upload = self.client.post("/convert/upload", files={"file": ("upload.txt", b"uploaded text")})
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(Path(upload.json()["output_path"]).read_text(encoding="utf-8"), "uploaded text")
        folder = self.client.post("/convert/folder", json={"folder_path": str(self.root), "engine": "markitdown"})
        self.assertEqual(folder.status_code, 200, folder.text)
        self.assertEqual(len(folder.json()["results"]), 1)
        self.assertEqual(folder.json()["results"][0]["status"], "ok")
        self.assertEqual(service.RESOURCE_CONTROLLER.snapshot()["completed"], 2)

    def test_actual_worker_mineru_images_survive_job_temp_cleanup(self) -> None:
        source = PROJECT / "tests/fixtures/real_documents/article-about-concurrency-and-correctness-with-local-resources.html"
        response = self.client.post("/convert/path", json={
            "file_path": str(source), "output_dir": str(self.root / "out"), "tier": "flash",
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["engine"], "mineru")
        self.assertFalse(data["fallback"])
        self.assertTrue(Path(data["images_dir"]).is_dir())
        self.assertTrue(list(Path(data["images_dir"]).iterdir()))
        markdown = Path(data["output_path"]).read_text(encoding="utf-8")
        self.assertIn("HTMLTokenResources", markdown)
        self.assertIn("images/", markdown)

    def test_actual_worker_errors_and_parent_write_conflict_keep_http_status(self) -> None:
        unknown = self.client.post("/convert/path", json={"file_path": str(self.source), "engine": "unknown"})
        self.assertEqual(unknown.status_code, 400, unknown.text)
        blocked = self.root / "blocked"
        blocked.write_text("user file", encoding="utf-8")
        conflict = self.client.post("/convert/path", json={"file_path": str(self.source), "output_dir": str(blocked)})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(blocked.read_text(encoding="utf-8"), "user file")
        self.assertEqual(service.RESOURCE_CONTROLLER.snapshot()["active"], 0)

    def test_large_local_file_and_batch_fail_before_scheduling(self) -> None:
        (self.root / "second.txt").write_text("second", encoding="utf-8")
        with patch.object(service, "RESOURCE_LIMITS", ResourceLimits(max_file_bytes=8)), \
                patch.object(service.RESOURCE_CONTROLLER, "convert") as convert:
            response = self.client.post("/convert/path", json={"file_path": str(self.source)})
        self.assertEqual(response.status_code, 413)
        convert.assert_not_called()
        with patch.object(service, "RESOURCE_LIMITS", ResourceLimits(max_batch_files=1)), \
                patch.object(service.RESOURCE_CONTROLLER, "convert") as convert:
            batch = self.client.post("/convert/folder", json={"folder_path": str(self.root)})
        self.assertEqual(batch.status_code, 413)
        convert.assert_not_called()
        self.assertFalse((self.root / "note.txt.docs2md").exists())

    def test_large_upload_cleans_partial_input_before_scheduling(self) -> None:
        before = set(Path(tempfile.gettempdir()).glob("docs2md_upload_*"))
        with patch.object(service, "RESOURCE_LIMITS", ResourceLimits(max_file_bytes=8)), \
                patch.object(service.RESOURCE_CONTROLLER, "convert") as convert:
            response = self.client.post("/convert/upload", files={"file": ("large.txt", b"123456789")})
        self.assertEqual(response.status_code, 413)
        convert.assert_not_called()
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("docs2md_upload_*")), before)

    def test_invalid_upload_names_are_rejected_before_input_or_worker_creation(self) -> None:
        for filename in (".", "..", "/", "\\", "note\x00.txt"):
            with self.subTest(filename=filename), patch.object(service.tempfile, "mkdtemp") as create, \
                    patch.object(service.RESOURCE_CONTROLLER, "convert") as convert:
                if "\x00" in filename:
                    # httpx percent-escapes NUL in filenames; send the actual
                    # malformed header bytes to exercise the server boundary.
                    body = (b'--upload-test\r\nContent-Disposition: form-data; name="file"; filename="'
                            + filename.encode() + b'"\r\n\r\ninput\r\n--upload-test--\r\n')
                    response = self.client.post("/convert/upload", content=body,
                                                headers={"Content-Type": "multipart/form-data; boundary=upload-test"})
                else:
                    response = self.client.post("/convert/upload", files={"file": (filename, b"input")})
                self.assertEqual(response.status_code, 400, response.text)
                create.assert_not_called()
                convert.assert_not_called()

    def test_upload_uses_portable_basename_for_relative_windows_paths(self) -> None:
        response = self.client.post("/convert/upload", files={"file": ("folder\\note.txt", b"input")},
                                    data={"output_dir": str(self.root / "uploads")})
        self.assertEqual(response.status_code, 200, response.text)
        output = self.root / "uploads" / "note.txt.docs2md" / "note.md"
        self.assertEqual(Path(response.json()["output_path"]), output)
        self.assertEqual(output.read_text(encoding="utf-8"), "input")

    def test_busy_and_deadline_errors_have_retryable_statuses(self) -> None:
        for error, expected in ((ResourceBusyError("queue_full", 2), 429), (ConversionTimeoutError("deadline"), 504)):
            with self.subTest(error=error), patch.object(service.RESOURCE_CONTROLLER, "convert", side_effect=error):
                response = self.client.post("/convert/path", json={"file_path": str(self.source)})
            self.assertEqual(response.status_code, expected)
            if expected == 429:
                self.assertEqual(response.headers["retry-after"], "2")

    def test_batch_aborts_on_overload_and_reports_per_file_deadlines(self) -> None:
        with patch.object(service.RESOURCE_CONTROLLER, "convert", side_effect=ResourceBusyError("queue_full", 1)):
            busy = self.client.post("/convert/folder", json={"folder_path": str(self.root)})
        self.assertEqual(busy.status_code, 429)
        self.assertEqual(busy.headers["retry-after"], "1")
        with patch.object(service.RESOURCE_CONTROLLER, "convert", side_effect=ConversionTimeoutError("deadline")):
            timeout = self.client.post("/convert/folder", json={"folder_path": str(self.root)})
        self.assertEqual(timeout.status_code, 200)
        self.assertEqual(timeout.json()["results"][0]["status_code"], 504)

    def test_health_exposes_consistent_limits_and_counters(self) -> None:
        with patch.object(service, "models_look_complete", return_value=False):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        resources = response.json()["resources"]
        self.assertEqual(resources["limits"]["max_file_bytes"], service.RESOURCE_LIMITS.max_file_bytes)
        self.assertEqual(resources["limits"]["max_json_body_bytes"], 65536)
        self.assertEqual(resources["limits"]["max_uploads"], 5)
        self.assertEqual((resources["active"], resources["pending"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
