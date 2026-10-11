"""Actual SQLite batch lifecycle, fairness, admission, and recovery regressions."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from fastapi import HTTPException  # noqa: E402
from batch_inputs import BatchInput, BatchInputPlan  # noqa: E402
from batch_jobs import BatchJobManager  # noqa: E402
from engines.base import ConvertOptions, ConvertStatusResponse, resolve_output_path, write_text_output  # noqa: E402


def wait_for(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the batch state")
        time.sleep(.01)


class BatchJobTests(unittest.TestCase):
    def setUp(self) -> None:
        (PROJECT / "tmp").mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="batch-jobs-", dir=PROJECT / "tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.release = threading.Event()
        self.managers = []
        self.addCleanup(self.stop_managers)

    def stop_managers(self) -> None:
        self.release.set()
        for manager in reversed(self.managers):
            manager.close()

    def plan(self, name="input", files=("a.txt",), *, recursive=False) -> BatchInputPlan:
        folder = self.root / name
        output = self.root / "output" / name
        folder.mkdir(parents=True, exist_ok=True)
        items = []
        for filename in files:
            source = folder / filename
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"content for {filename}", encoding="utf-8")
            relative = Path(filename)
            items.append(BatchInput(source, relative, output / relative.parent, source.stat().st_size))
        return BatchInputPlan(folder, output, recursive, tuple(items), len(items))

    def successful(self, source, *, engine_name, output_dir, options):
        base, output = resolve_output_path(source, output_dir)
        write_text_output(output, source.read_text(encoding="utf-8"))
        return ConvertStatusResponse(True, engine_name or "test", str(output), str(base), None, False, message="saved")

    def manager(self, converter=None, *, state=None, start=True, **kwargs) -> BatchJobManager:
        manager = BatchJobManager(state or self.root / "state", converter or self.successful, **kwargs)
        self.managers.append(manager)
        if start:
            manager.start()
        return manager

    def submit(self, manager, plan, **kwargs) -> dict:
        return manager.submit(plan, options=ConvertOptions(), **kwargs)

    def terminal(self, manager, job) -> dict:
        job_id = job["job_id"] if isinstance(job, dict) else job
        wait_for(lambda: manager.get(job_id)["status"] in ("completed", "completed_with_errors", "cancelled", "paused"))
        return manager.get(job_id)

    def test_constructor_does_no_io_and_start_is_idempotent(self) -> None:
        state = self.root / "lazy-state"
        with patch("batch_jobs.sqlite3.connect", side_effect=AssertionError("constructor must not connect")), patch("batch_jobs.threading.Thread", side_effect=AssertionError("constructor must not create a thread")):
            manager = self.manager(state=state, start=False)
        self.assertFalse(state.exists())
        self.assertFalse(manager.snapshot()["scheduler_running"])
        with self.assertRaises(HTTPException) as error:
            manager.list_jobs()
        self.assertEqual(error.exception.status_code, 503)
        manager.start()
        original = manager._thread
        manager.start()
        self.assertIs(manager._thread, original)
        self.assertTrue(manager.snapshot()["scheduler_running"])

    def test_empty_batch_finishes_without_dispatch(self) -> None:
        calls = []
        manager = self.manager(lambda *args, **kwargs: calls.append(args))
        job = self.submit(manager, self.plan(files=()))
        self.assertEqual(job["status"], "completed")
        self.assertEqual((job["total"], job["processed"], job["items"]), (0, 0, []))
        self.assertFalse(calls)

    def test_success_counts_results_and_auto_engine_choices_are_frozen(self) -> None:
        calls = []
        selected = []
        current = {"name": "mineru"}
        def select(source):
            selected.append(source.name)
            return current["name"]
        def convert(source, **kwargs):
            calls.append((source.name, kwargs["engine_name"], kwargs["options"].mineru_tier, kwargs["output_dir"]))
            if len(calls) == 1:
                self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert, select_engine=select)
        plan = self.plan(files=("a.txt", "nested/b.txt"), recursive=True)
        job = manager.submit(plan, options=ConvertOptions(mineru_tier="flash"))
        wait_for(lambda: len(calls) == 1)
        current["name"] = "markitdown"
        self.release.set()
        done = self.terminal(manager, job)
        self.assertEqual(done["status"], "completed")
        self.assertEqual((done["total"], done["processed"], done["succeeded"], done["failed"]), (2, 2, 2, 0))
        self.assertEqual([call[1] for call in calls], ["mineru", "mineru"])
        self.assertEqual([call[2] for call in calls], ["flash", "flash"])
        self.assertEqual(selected, ["a.txt", "b.txt"])
        self.assertEqual(done["items"][1]["relative_path"], "nested/b.txt")
        self.assertEqual(calls[1][3], plan.output_dir / "nested")
        for item in done["items"]:
            self.assertTrue(Path(item["result"]["output_path"]).is_file())

    def test_retry_only_failed_files_and_keeps_successful_outputs(self) -> None:
        calls = []
        fixed = {"value": False}
        def convert(source, **kwargs):
            calls.append(source.name)
            if source.name == "b.txt" and not fixed["value"]:
                raise HTTPException(409, "locked " + "x" * 3000)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        job = self.submit(manager, self.plan(files=("a.txt", "b.txt")))
        failed = self.terminal(manager, job)
        self.assertEqual(failed["status"], "completed_with_errors")
        self.assertEqual(failed["items"][1]["status_code"], 409)
        self.assertEqual(len(failed["items"][1]["detail"]), 2048)
        original = Path(failed["items"][0]["result"]["output_path"]).read_bytes()
        fixed["value"] = True
        manager.retry(job["job_id"])
        done = self.terminal(manager, job)
        self.assertEqual(calls, ["a.txt", "b.txt", "b.txt"])
        self.assertEqual([item["attempts"] for item in done["items"]], [1, 2])
        self.assertEqual(done["succeeded"], 2)
        self.assertEqual(Path(done["items"][0]["result"]["output_path"]).read_bytes(), original)
        with self.assertRaises(HTTPException) as error:
            manager.retry(job["job_id"])
        self.assertEqual(error.exception.status_code, 409)

    def test_cancel_running_batch_finishes_current_file_and_stops_remaining(self) -> None:
        calls = []
        def convert(source, **kwargs):
            calls.append(source.name)
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        job = self.submit(manager, self.plan(files=("a.txt", "b.txt")))
        wait_for(lambda: len(calls) == 1)
        cancelling = manager.cancel(job["job_id"])
        self.assertEqual(cancelling["status"], "cancelling")
        self.assertTrue(cancelling["cancel_requested"])
        self.assertEqual((cancelling["running"], cancelling["cancelled"]), (1, 1))
        with self.assertRaises(HTTPException) as error:
            manager.retry(job["job_id"], include_cancelled=True)
        self.assertEqual(error.exception.status_code, 409)
        self.release.set()
        done = self.terminal(manager, job)
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual((done["succeeded"], done["cancelled"], done["processed"]), (1, 1, 2))
        self.assertEqual(calls, ["a.txt"])
        self.assertTrue(Path(done["items"][0]["result"]["output_path"]).exists())

    def test_cancel_queued_batch_and_opt_in_cancelled_retry(self) -> None:
        calls = []
        def convert(source, **kwargs):
            calls.append(source.parent.name)
            if source.parent.name == "first":
                self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        first = self.submit(manager, self.plan("first"))
        wait_for(lambda: len(calls) == 1)
        second = self.submit(manager, self.plan("second"))
        cancelled = manager.cancel(second["job_id"])
        self.assertEqual((cancelled["status"], cancelled["cancelled"], cancelled["running"]), ("cancelled", 1, 0))
        with self.assertRaises(HTTPException) as error:
            manager.retry(second["job_id"])
        self.assertEqual(error.exception.status_code, 409)
        manager.retry(second["job_id"], include_cancelled=True)
        self.release.set()
        self.terminal(manager, first)
        self.assertEqual(self.terminal(manager, second)["status"], "completed")
        self.assertEqual(calls, ["first", "second"])

    def test_one_dispatcher_rotates_between_batches(self) -> None:
        order = []
        def convert(source, **kwargs):
            order.append((source.parent.name, source.name))
            if len(order) == 1:
                self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        a = self.submit(manager, self.plan("A", ("a.txt", "b.txt")))
        wait_for(lambda: len(order) == 1)
        b = self.submit(manager, self.plan("B", ("a.txt", "b.txt")))
        c = self.submit(manager, self.plan("C", ("a.txt", "b.txt")))
        self.release.set()
        for job in (a, b, c):
            self.terminal(manager, job)
        self.assertEqual(order, [("A", "a.txt"), ("B", "a.txt"), ("C", "a.txt"), ("A", "b.txt"), ("B", "b.txt"), ("C", "b.txt")])

    def test_active_admission_is_bounded_and_idempotent_lookup_works_when_full(self) -> None:
        def convert(source, **kwargs):
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert, max_active_jobs=1, max_history_jobs=2)
        identity = {"folder_path": "canonical", "tier": "flash"}
        plan = self.plan()
        job = self.submit(manager, plan, idempotency_key="same", request_identity=identity)
        wait_for(lambda: manager.get(job["job_id"])["running"] == 1)
        with self.assertRaises(HTTPException) as error:
            self.submit(manager, self.plan("other"))
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.headers["Retry-After"], "1")
        plan.items[0].path.unlink()
        self.assertEqual(manager.lookup_idempotent("same", identity)["job_id"], job["job_id"])
        repeated = self.submit(manager, plan, idempotency_key="same", request_identity=identity)
        self.assertEqual(repeated["job_id"], job["job_id"])
        with self.assertRaises(HTTPException) as conflict:
            manager.lookup_idempotent("same", {"folder_path": "different"})
        self.assertEqual(conflict.exception.status_code, 409)

    def test_concurrent_same_key_submissions_create_only_one_job(self) -> None:
        barrier = threading.Barrier(2)
        def select(source):
            barrier.wait(3)
            return "test"
        manager = self.manager(select_engine=select)
        plan = self.plan()
        jobs = []
        errors = []
        def submit():
            try:
                jobs.append(self.submit(manager, plan, idempotency_key="concurrent"))
            except BaseException as exc:
                errors.append(exc)
        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["job_id"], jobs[1]["job_id"])
        self.assertEqual(manager.snapshot()["total"], 1)

    def test_history_prunes_old_terminal_metadata_and_preserves_output_files(self) -> None:
        manager = self.manager(max_active_jobs=1, max_history_jobs=2)
        jobs = []
        for index in range(3):
            job = self.submit(manager, self.plan(f"input-{index}"), idempotency_key=f"key-{index}")
            jobs.append(self.terminal(manager, job))
        with self.assertRaises(HTTPException) as missing:
            manager.get(jobs[0]["job_id"])
        self.assertEqual(missing.exception.status_code, 404)
        self.assertTrue(Path(jobs[0]["items"][0]["result"]["output_path"]).is_file())
        self.assertEqual(manager.snapshot()["total"], 2)
        listed = manager.list_jobs(limit=1)
        self.assertEqual(listed[0]["job_id"], jobs[2]["job_id"])
        self.assertNotIn("items", listed[0])
        with closing(sqlite3.connect(manager.state_dir / "jobs.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)

    def test_same_directory_owner_lock_prevents_live_recovery(self) -> None:
        def convert(source, **kwargs):
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        job = self.submit(manager, self.plan())
        wait_for(lambda: manager.get(job["job_id"])["running"] == 1)
        second = self.manager(start=False)
        with self.assertRaises(HTTPException) as error:
            second.start()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(manager.get(job["job_id"])["items"][0]["status"], "running")
        self.release.set()
        self.terminal(manager, job)
        manager.close()
        second.start()
        self.assertEqual(second.get(job["job_id"])["succeeded"], 1)

    def seed_interrupted(self, manager, job, *, cancel=False):
        manager.close()
        with closing(sqlite3.connect(manager.state_dir / "jobs.sqlite3")) as connection, connection:
            connection.execute("UPDATE jobs SET status=?,cancel_requested=? WHERE job_id=?", ("cancelling" if cancel else "running", int(cancel), job["job_id"]))
            connection.execute("UPDATE items SET status='running',result_json=NULL WHERE job_id=? AND ordinal=1", (job["job_id"],))
            connection.execute("UPDATE items SET status='pending',result_json=NULL,attempts=0 WHERE job_id=? AND ordinal=2", (job["job_id"],))

    def test_recovery_keeps_successes_and_only_resumes_frozen_pending_files(self) -> None:
        manager = self.manager()
        plan = self.plan(files=("a.txt", "b.txt", "c.txt"))
        job = self.submit(manager, plan)
        self.terminal(manager, job)
        self.seed_interrupted(manager, job)
        (plan.folder / "new.txt").write_text("not in the original manifest")
        calls = []
        def convert(source, **kwargs):
            calls.append(source.name)
            return self.successful(source, **kwargs)
        restored = self.manager(convert)
        recovered = self.terminal(restored, job)
        self.assertEqual(calls, ["c.txt"])
        self.assertEqual(recovered["status"], "completed_with_errors")
        self.assertEqual(recovered["total"], 3)
        self.assertEqual(recovered["items"][1]["status"], "interrupted")
        self.assertEqual(recovered["items"][1]["status_code"], 503)
        restored.retry(job["job_id"])
        self.assertEqual(self.terminal(restored, job)["succeeded"], 3)
        self.assertEqual(calls, ["c.txt", "b.txt"])

    def test_recovery_honors_cancel_request_and_does_not_dispatch_pending(self) -> None:
        manager = self.manager()
        job = self.submit(manager, self.plan(files=("a.txt", "b.txt", "c.txt")))
        self.terminal(manager, job)
        self.seed_interrupted(manager, job, cancel=True)
        calls = []
        restored = self.manager(lambda *args, **kwargs: calls.append(args))
        recovered = restored.get(job["job_id"])
        self.assertEqual(recovered["status"], "cancelled")
        self.assertEqual([item["status"] for item in recovered["items"]], ["succeeded", "interrupted", "cancelled"])
        self.assertFalse(calls)

    def test_close_waits_for_current_file_and_preserves_pending_for_restart(self) -> None:
        calls = []
        def convert(source, **kwargs):
            calls.append(source.name)
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        job = self.submit(manager, self.plan(files=("a.txt", "b.txt")))
        wait_for(lambda: len(calls) == 1)
        closing = threading.Thread(target=manager.close)
        closing.start()
        wait_for(lambda: manager._stopping)
        self.assertTrue(closing.is_alive())
        with self.assertRaises(HTTPException) as error:
            self.submit(manager, self.plan("new"))
        self.assertEqual(error.exception.status_code, 503)
        self.release.set()
        closing.join(5)
        self.assertFalse(closing.is_alive())
        self.assertEqual(calls, ["a.txt"])
        resumed_calls = []
        def resumed(source, **kwargs):
            resumed_calls.append(source.name)
            return self.successful(source, **kwargs)
        restored = self.manager(resumed)
        self.assertEqual(self.terminal(restored, job)["succeeded"], 2)
        self.assertEqual(resumed_calls, ["b.txt"])

    def test_three_admission_rejections_pause_and_manual_retry_resets_budget(self) -> None:
        busy = {"value": True}
        calls = []
        def convert(source, **kwargs):
            calls.append(source.name)
            if busy["value"]:
                raise HTTPException(429, "queue full", headers={"Retry-After": "0"})
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        job = self.submit(manager, self.plan())
        paused = self.terminal(manager, job)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual((paused["pending"], paused["failed"], paused["items"][0]["attempts"]), (1, 0, 0))
        self.assertEqual(paused["items"][0]["status_code"], 429)
        self.assertEqual(len(calls), 3)
        self.assertEqual(manager.snapshot()["active"], 0)
        busy["value"] = False
        manager.retry(job["job_id"])
        done = self.terminal(manager, job)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["items"][0]["attempts"], 1)
        self.assertEqual(len(calls), 4)

    def test_busy_backoff_does_not_block_other_batches(self) -> None:
        order = []
        starts = []
        def convert(source, **kwargs):
            order.append(source.parent.name)
            if source.parent.name == "A":
                starts.append(time.monotonic())
                if len(starts) == 1:
                    self.release.wait(5)
                    raise HTTPException(429, "busy", headers={"Retry-After": ".35"})
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        a = self.submit(manager, self.plan("A"))
        wait_for(lambda: len(order) == 1)
        b = self.submit(manager, self.plan("B"))
        self.release.set()
        self.terminal(manager, b)
        self.terminal(manager, a)
        self.assertEqual(order, ["A", "B", "A"])
        self.assertGreaterEqual(starts[1] - starts[0], .30)

    def test_paused_jobs_are_not_pruned_and_can_be_cancelled_to_free_history(self) -> None:
        def busy(*args, **kwargs):
            raise HTTPException(429, "busy", headers={"Retry-After": "0"})
        manager = self.manager(busy, max_active_jobs=1, max_history_jobs=1)
        first = self.submit(manager, self.plan("first"))
        self.assertEqual(self.terminal(manager, first)["status"], "paused")
        with self.assertRaises(HTTPException) as error:
            self.submit(manager, self.plan("second"))
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(manager.get(first["job_id"])["status"], "paused")
        manager.cancel(first["job_id"])
        second = self.submit(manager, self.plan("second"))
        self.assertNotEqual(second["job_id"], first["job_id"])
        with self.assertRaises(HTTPException) as missing:
            manager.get(first["job_id"])
        self.assertEqual(missing.exception.status_code, 404)

    def test_retry_obeys_active_budget_without_mutating_paused_job(self) -> None:
        def convert(source, **kwargs):
            if source.parent.name == "paused":
                raise HTTPException(429, "busy", headers={"Retry-After": "0"})
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert, max_active_jobs=1, max_history_jobs=2)
        paused = self.submit(manager, self.plan("paused"))
        self.terminal(manager, paused)
        active = self.submit(manager, self.plan("active"))
        wait_for(lambda: manager.get(active["job_id"])["running"] == 1)
        with self.assertRaises(HTTPException) as error:
            manager.retry(paused["job_id"])
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(manager.get(paused["job_id"])["status"], "paused")

    def test_unexpected_converter_exception_is_persisted_and_bounded(self) -> None:
        def fail(*args, **kwargs):
            raise RuntimeError("x" * 3000)
        manager = self.manager(fail)
        done = self.terminal(manager, self.submit(manager, self.plan()))
        self.assertEqual(done["status"], "completed_with_errors")
        self.assertEqual(done["items"][0]["status_code"], 500)
        self.assertEqual(len(done["items"][0]["detail"]), 2048)

    def test_failed_sqlite_finish_stops_admission_and_restart_requires_explicit_retry(self) -> None:
        calls = []
        def convert(source, **kwargs):
            calls.append(source.name)
            self.release.wait(5)
            return self.successful(source, **kwargs)
        manager = self.manager(convert)
        plan = self.plan(files=("a.txt", "b.txt"))
        job = self.submit(manager, plan)
        wait_for(lambda: len(calls) == 1)
        with closing(sqlite3.connect(manager.state_dir / "jobs.sqlite3")) as connection, connection:
            connection.execute("""
                CREATE TRIGGER fail_result BEFORE UPDATE OF result_json ON items
                WHEN NEW.status='succeeded'
                BEGIN SELECT RAISE(ABORT, 'database or disk full'); END
            """)
        self.release.set()
        wait_for(lambda: not manager.snapshot()["scheduler_running"])
        self.assertIn("database or disk full", manager.snapshot()["scheduler_error"])
        uncertain = manager.get(job["job_id"])
        self.assertEqual((uncertain["running"], uncertain["pending"], uncertain["succeeded"]), (1, 1, 0))
        self.assertEqual(manager.list_jobs()[0]["job_id"], job["job_id"])
        for action in (
            lambda: self.submit(manager, plan),
            lambda: manager.retry(job["job_id"]),
            lambda: manager.cancel(job["job_id"]),
        ):
            with self.assertRaises(HTTPException) as error:
                action()
            self.assertEqual(error.exception.status_code, 503)
        manager.close()
        with closing(sqlite3.connect(manager.state_dir / "jobs.sqlite3")) as connection, connection:
            connection.execute("DROP TRIGGER fail_result")
        manager.start()
        done = self.terminal(manager, job)
        self.assertIsNone(manager.snapshot()["scheduler_error"])
        self.assertEqual(done["items"][0]["status"], "interrupted")
        self.assertEqual(done["items"][0]["status_code"], 503)
        self.assertEqual(calls, ["a.txt", "b.txt"])
        manager.retry(job["job_id"])
        self.assertEqual(self.terminal(manager, job)["succeeded"], 2)
        self.assertEqual(calls, ["a.txt", "b.txt", "a.txt"])

    def test_claim_operational_error_is_bounded_and_readable(self) -> None:
        manager = self.manager()
        job = self.submit(manager, self.plan())
        self.terminal(manager, job)
        with patch.object(manager, "_claim", side_effect=sqlite3.OperationalError("disk full " * 500)):
            with manager._condition:
                manager._condition.notify_all()
            wait_for(lambda: not manager.snapshot()["scheduler_running"])
        snapshot = manager.snapshot()
        self.assertEqual(len(snapshot["scheduler_error"]), 2048)
        self.assertTrue(snapshot["scheduler_error"].startswith("OperationalError:"))
        self.assertEqual(manager.get(job["job_id"])["status"], "completed")
        with self.assertRaises(HTTPException) as error:
            self.submit(manager, self.plan("later"))
        self.assertEqual(error.exception.status_code, 503)
        manager.close()
        manager.start()
        self.assertIsNone(manager.snapshot()["scheduler_error"])
        self.assertEqual(self.terminal(manager, self.submit(manager, self.plan("later")))["status"], "completed")

    def test_public_validation_and_unknown_job_errors(self) -> None:
        manager = self.manager()
        for call in (lambda: manager.get("missing"), lambda: manager.cancel("missing"), lambda: manager.retry("missing")):
            with self.assertRaises(HTTPException) as error:
                call()
            self.assertEqual(error.exception.status_code, 404)
        for limit in (0, 101, True):
            with self.assertRaises(HTTPException) as error:
                manager.list_jobs(limit)
            self.assertEqual(error.exception.status_code, 400)
        for key in ("", "x" * 129, "中文"):
            with self.assertRaises(HTTPException) as error:
                manager.lookup_idempotent(key, {})
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            BatchJobManager(self.root, self.successful, max_active_jobs=2, max_history_jobs=1)


if __name__ == "__main__":
    unittest.main()
