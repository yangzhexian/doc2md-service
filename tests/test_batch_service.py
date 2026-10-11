"""Batch HTTP workflows, persistent progress, and actual isolated conversions."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import converter_service as service  # noqa: E402
from batch_jobs import BatchJobManager  # noqa: E402
from engines.base import ConvertStatusResponse, resolve_output_path, write_text_output  # noqa: E402
from resource_control import ResourceConfig, ResourceController  # noqa: E402
from test_real_documents import isolated_environment  # noqa: E402

TERMINAL = {"completed", "completed_with_errors", "cancelled", "paused"}


class BatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = self.stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="docs2md_batch_api_"))).resolve()
        stack.enter_context(patch.dict(os.environ, isolated_environment(self.root), clear=True))
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        stack.enter_context(patch.object(service, "write_mineru_config"))
        stack.enter_context(patch.object(service, "models_look_complete", return_value=False))
        stack.enter_context(patch.object(service, "RESOURCE_CONTROLLER",
                                        ResourceController(ResourceConfig(conversion_timeout_seconds=60))))
        self.manager = BatchJobManager(self.root / "state", lambda path, **kwargs: service._run_conversion(path, **kwargs),
                                       max_active_jobs=2, max_history_jobs=8,
                                       select_engine=lambda path: service._pick_engine(None, path))
        stack.enter_context(patch.object(service, "BATCH_MANAGER", self.manager))
        self.client = stack.enter_context(TestClient(service.app))

    def source(self, relative: str, text: str = "input") -> Path:
        path = self.inputs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def save(self, path, *, engine_name=None, output_dir=None, options=None) -> ConvertStatusResponse:
        directory, output = resolve_output_path(path, output_dir)
        write_text_output(output, path.read_text(encoding="utf-8"))
        return ConvertStatusResponse(True, engine_name or "markitdown", str(output), str(directory), None, False,
                                     message=f"Saved to {output}")

    def submit(self, **fields):
        body = {"folder_path": str(self.inputs), "engine": "markitdown", **fields}
        response = self.client.post("/batches", json=body)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.headers["location"], f"/batches/{response.json()['job_id']}")
        return response.json()

    def wait(self, job_id: str, expected=TERMINAL) -> dict:
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            response = self.client.get(f"/batches/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["status"] in expected:
                return job
            time.sleep(.02)
        self.fail(f"Batch did not reach {expected}: {job}")

    def test_actual_recursive_job_preserves_sources_and_distinct_outputs(self) -> None:
        first = self.source("a/report.txt", "Alpha batch document")
        second = self.source("b/report.txt", "Beta batch document")
        generated = self.inputs / "generated"
        generated.mkdir()
        (generated / "old.txt").write_text("not an input", encoding="utf-8")
        self.source("a/old.txt.docs2md/old.txt", "previous generated output")
        job = self.wait(self.submit(recursive=True, output_dir=str(generated), extensions=["TXT"])["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual((job["total"], job["succeeded"], job["failed"]), (2, 2, 0))
        paths = [Path(item["result"]["output_path"]) for item in job["items"]]
        self.assertEqual(paths, [generated / "a/report.txt.docs2md/report.md",
                                 generated / "b/report.txt.docs2md/report.md"])
        self.assertEqual([path.read_text(encoding="utf-8") for path in paths],
                         ["Alpha batch document", "Beta batch document"])
        self.assertEqual(first.read_text(encoding="utf-8"), "Alpha batch document")
        self.assertEqual(second.read_text(encoding="utf-8"), "Beta batch document")
        self.assertEqual(service.RESOURCE_CONTROLLER.snapshot()["completed"], 2)
        single = self.client.post("/convert/path", json={"file_path": str(first), "engine": "markitdown"})
        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(service.RESOURCE_CONTROLLER.snapshot()["completed"], 3)
        listed = self.client.get("/batches").json()["jobs"]
        self.assertEqual(listed[0]["job_id"], job["job_id"])
        self.assertNotIn("items", listed[0])
        self.assertEqual(self.client.get("/health").json()["resources"]["batches"], self.manager.snapshot())

    def test_sync_recursive_filters_and_uses_separate_options_per_file(self) -> None:
        self.source("a/report.txt", "first")
        self.source("b/report.txt", "second")
        self.source("b/unselected.csv", "ignored")
        options = []
        def convert(path, **kwargs):
            self.assertEqual(kwargs["options"].output_dir, kwargs["output_dir"])
            options.append(kwargs["options"])
            return self.save(path, **kwargs)
        with patch.object(service, "_run_conversion", side_effect=convert):
            response = self.client.post("/convert/folder", json={"folder_path": str(self.inputs),
                                                                 "recursive": True, "extensions": ["txt"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"], {"total": 2, "processed": 2, "succeeded": 2, "failed": 0, "pending": 0})
        self.assertIsNot(options[0], options[1])
        self.assertEqual((self.inputs / "a/report.txt.docs2md/report.md").read_text(), "first")
        self.assertEqual((self.inputs / "b/report.txt.docs2md/report.md").read_text(), "second")

    def test_sync_overload_retains_completed_results_and_remaining_files(self) -> None:
        first = self.source("a.txt")
        second = self.source("b.txt")
        def convert(path, **kwargs):
            if path == second:
                raise HTTPException(429, "busy", headers={"Retry-After": "2"})
            return self.save(path, **kwargs)
        with patch.object(service, "_run_conversion", side_effect=convert):
            response = self.client.post("/convert/folder", json={"folder_path": str(self.inputs)})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "2")
        detail = response.json()["detail"]
        self.assertEqual(detail["pending_files"], [str(second)])
        self.assertEqual(detail["results"][0]["file"], str(first))
        self.assertEqual(detail["summary"], {"total": 2, "processed": 1, "succeeded": 1, "failed": 0, "pending": 1})
        self.assertTrue(Path(detail["results"][0]["output_path"]).is_file())

    def test_failed_retry_reads_current_source_and_preserves_success(self) -> None:
        self.source("a.txt", "successful")
        failed = self.source("b.txt", "old input")
        reject = True
        def convert(path, **kwargs):
            if path == failed and reject:
                raise HTTPException(504, "document deadline")
            return self.save(path, **kwargs)
        with patch.object(service, "_run_conversion", side_effect=convert):
            job = self.wait(self.submit()["job_id"])
            self.assertEqual(job["status"], "completed_with_errors")
            self.assertEqual(job["items"][1]["status_code"], 504)
            success = Path(job["items"][0]["result"]["output_path"])
            original_time = success.stat().st_mtime_ns
            failed.write_text("updated input", encoding="utf-8")
            reject = False
            retry = self.client.post(f"/batches/{job['job_id']}/retry")
            self.assertEqual(retry.status_code, 202, retry.text)
            job = self.wait(job["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual([item["attempts"] for item in job["items"]], [1, 2])
        self.assertEqual(success.stat().st_mtime_ns, original_time)
        self.assertEqual(Path(job["items"][1]["result"]["output_path"]).read_text(), "updated input")
        self.assertEqual(self.client.post(f"/batches/{job['job_id']}/retry").status_code, 409)

    def test_running_cancel_finishes_current_file_and_can_resume_cancelled_items(self) -> None:
        first = self.source("a.txt")
        self.source("b.txt")
        entered, release = threading.Event(), threading.Event()
        calls = []
        def convert(path, **kwargs):
            calls.append(path)
            if path == first:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Test must release the active document")
            return self.save(path, **kwargs)
        with patch.object(service, "_run_conversion", side_effect=convert):
            try:
                job = self.submit()
                self.assertTrue(entered.wait(5))
                cancel = self.client.post(f"/batches/{job['job_id']}/cancel")
                self.assertEqual(cancel.status_code, 200, cancel.text)
                self.assertEqual(cancel.json()["status"], "cancelling")
            finally:
                release.set()
            job = self.wait(job["job_id"])
            self.assertEqual(job["status"], "cancelled")
            self.assertEqual([item["status"] for item in job["items"]], ["succeeded", "cancelled"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.client.post(f"/batches/{job['job_id']}/retry").status_code, 409)
            retry = self.client.post(f"/batches/{job['job_id']}/retry", json={"include_cancelled": True})
            self.assertEqual(retry.status_code, 202, retry.text)
            job = self.wait(job["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(calls), 2)
        self.assertEqual([item["attempts"] for item in job["items"]], [1, 1])

    def test_idempotency_returns_existing_job_before_filesystem_preflight(self) -> None:
        source = self.source("note.txt")
        body = {"folder_path": str(self.inputs), "output_dir": str(self.root / "out"), "engine": "markitdown"}
        headers = {"Idempotency-Key": "safe-submit-key"}
        with patch.object(service, "_run_conversion", side_effect=self.save):
            response = self.client.post("/batches", json=body, headers=headers)
            self.assertEqual(response.status_code, 202, response.text)
            original = self.wait(response.json()["job_id"])
        source.unlink()
        self.inputs.rmdir()
        with patch.object(service, "plan_folder_inputs") as scan:
            duplicate = self.client.post("/batches", json=body, headers=headers)
            conflict = self.client.post("/batches", json={**body, "recursive": True}, headers=headers)
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertEqual(duplicate.json()["job_id"], original["job_id"])
        self.assertEqual(conflict.status_code, 409)
        scan.assert_not_called()

    def test_all_batch_command_bodies_have_the_json_size_guard(self) -> None:
        for path in ("/batches", "/batches/unknown/retry", "/batches/unknown/cancel"):
            with self.subTest(path=path):
                response = self.client.post(path, content=b" " * 65537,
                                            headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.manager.list_jobs(), [])

    def test_streamed_batch_command_bodies_cannot_bypass_the_size_guard(self) -> None:
        for path in ("/batches", "/batches/unknown/retry", "/batches/unknown/cancel"):
            for lengths in ({}, {"Content-Length": "1"}):
                with self.subTest(path=path, headers=lengths):
                    response = self.client.post(path, content=iter((b" " * 65536, b"{}")),
                                                headers={"Content-Type": "application/json", **lengths})
                    self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.manager.list_jobs(), [])

    def test_empty_batches_and_request_errors_have_defined_responses(self) -> None:
        self.assertEqual(self.client.get("/batches/missing").status_code, 404)
        self.assertEqual(self.client.post("/batches/missing/retry").status_code, 404)
        self.assertEqual(self.client.post("/batches/missing/cancel").status_code, 404)
        self.assertEqual(self.client.get("/batches?limit=0").status_code, 422)
        self.assertEqual(self.client.post("/batches", json={"folder_path": str(self.root / "missing")}).status_code, 404)
        self.assertEqual(self.client.post("/batches", json={"folder_path": str(self.inputs), "engine": "unknown"}).status_code, 400)
        self.assertEqual(self.client.post("/batches", json={"folder_path": str(self.inputs), "extensions": []}).status_code, 400)
        self.assertEqual(self.client.post("/batches", json={"folder_path": str(self.inputs)},
                                         headers={"Idempotency-Key": " "}).status_code, 400)
        job = self.wait(self.submit()["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual((job["total"], job["processed"]), (0, 0))
        self.assertEqual(self.client.post(f"/batches/{job['job_id']}/cancel", json={"unexpected": True}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
