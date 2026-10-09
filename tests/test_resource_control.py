"""Admission, worker transport, and actual process-tree resource regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from engines.base import ConvertOptions, ConvertStatusResponse, OutputWriteError  # noqa: E402
from resource_control import (  # noqa: E402
    ConversionTimeoutError, ResourceBusyError, ResourceConfig, ResourceController,
    WorkerHTTPError, run_supervised_process, worker_environment,
)
import resource_control  # noqa: E402


def result(task) -> ConvertStatusResponse:
    return ConvertStatusResponse(True, "test", task.file_path + ".md", "", None, False)


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the test condition")
        time.sleep(0.01)


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    # A killed Linux child can await adoption/reaping; zombies consume no work.
    state = Path(f"/proc/{pid}/stat")
    if state.exists():
        try:
            return state.read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_explicit_settings(self) -> None:
        self.assertEqual(ResourceConfig.from_env({}), ResourceConfig())
        config = ResourceConfig.from_env({
            "DOCS2MD_MAX_CONCURRENT_CONVERSIONS": "2",
            "DOCS2MD_MAX_PENDING_CONVERSIONS": "0",
            "DOCS2MD_QUEUE_TIMEOUT_SECONDS": ".25",
            "DOCS2MD_CONVERSION_TIMEOUT_SECONDS": "8.5",
            "DOCS2MD_WORKER_THREADS": "3",
        })
        self.assertEqual(config, ResourceConfig(2, 0, .25, 8.5, 3))

    def test_invalid_environment_fails_instead_of_silently_disabling_limits(self) -> None:
        integers = {
            "DOCS2MD_MAX_CONCURRENT_CONVERSIONS": ["0", "-1", "1.5", "", "nan"],
            "DOCS2MD_MAX_PENDING_CONVERSIONS": ["-1", "1.5", "", "nan"],
            "DOCS2MD_WORKER_THREADS": ["0", "-1", "1.5", "", "nan"],
            "DOCS2MD_QUEUE_TIMEOUT_SECONDS": ["0", "-1", "inf", "nan", ""],
            "DOCS2MD_CONVERSION_TIMEOUT_SECONDS": ["0", "-1", "-inf", "nan", ""],
        }
        for name, values in integers.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, name):
                    ResourceConfig.from_env({name: value})

    def test_direct_configuration_is_also_validated(self) -> None:
        for override in (
            {"max_concurrent": True}, {"max_pending": -1}, {"worker_threads": 1.5},
            {"queue_timeout_seconds": float("nan")}, {"conversion_timeout_seconds": False},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                ResourceConfig(**override)

    def test_worker_environment_overrides_native_pools_and_isolates_temporary_files(self) -> None:
        temporary = Path("worker-owned-tmp")
        environment = worker_environment(ResourceConfig(worker_threads=3), temporary)
        for key in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            self.assertEqual(environment[key], "3")
        for key in ("TMP", "TEMP", "TMPDIR"):
            self.assertEqual(environment[key], str(temporary))
        self.assertEqual(environment["TOKENIZERS_PARALLELISM"], "false")
        self.assertEqual(environment["DOCS2MD_WORKER_THREADS"], "3")
        self.assertEqual(environment["MINERU_INTRA_OP_NUM_THREADS"], "3")
        self.assertEqual(environment["MINERU_INTER_OP_NUM_THREADS"], "1")


class AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.errors = []
        self.threads = []
        self.release = threading.Event()
        self.addCleanup(self.finish_threads)

    def finish_threads(self) -> None:
        self.release.set()
        for thread in self.threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), "Test conversion did not stop")

    def start(self, controller: ResourceController, name: str, **kwargs) -> threading.Thread:
        def convert():
            try:
                controller.convert(Path(name), **kwargs)
            except BaseException as exc:
                self.errors.append(exc)
        thread = threading.Thread(target=convert)
        self.threads.append(thread)
        thread.start()
        return thread

    def test_fifo_and_queue_full_do_not_start_rejected_work(self) -> None:
        order = []
        def execute(task, config):
            order.append(task.file_path)
            if task.file_path == "first.txt":
                self.release.wait(5)
            return result(task)
        controller = ResourceController(ResourceConfig(max_pending=2), execute)
        self.start(controller, "first.txt")
        wait_for(lambda: controller.snapshot()["active"] == 1)
        self.start(controller, "second.txt")
        wait_for(lambda: controller.snapshot()["pending"] == 1)
        self.start(controller, "third.txt")
        wait_for(lambda: controller.snapshot()["pending"] == 2)
        with self.assertRaises(ResourceBusyError) as error:
            controller.convert(Path("rejected.txt"))
        self.assertEqual(error.exception.reason, "queue_full")
        self.assertEqual(error.exception.retry_after, 30)
        self.release.set()
        self.finish_threads()
        self.assertEqual(order, ["first.txt", "second.txt", "third.txt"])
        self.assertFalse(self.errors)
        snapshot = controller.snapshot()
        self.assertEqual((snapshot["active"], snapshot["pending"], snapshot["completed"], snapshot["rejected"]), (0, 0, 3, 1))

    def test_zero_pending_capacity_rejects_immediately(self) -> None:
        def execute(task, config):
            self.release.wait(5)
            return result(task)
        controller = ResourceController(ResourceConfig(max_pending=0), execute)
        self.start(controller, "active.txt")
        wait_for(lambda: controller.snapshot()["active"] == 1)
        started = time.monotonic()
        with self.assertRaises(ResourceBusyError):
            controller.convert(Path("rejected.txt"))
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(controller.snapshot()["pending"], 0)

    def test_queue_timeout_removes_waiter_and_recovers_capacity(self) -> None:
        def execute(task, config):
            if task.file_path == "active.txt":
                self.release.wait(5)
            return result(task)
        controller = ResourceController(ResourceConfig(queue_timeout_seconds=.1), execute)
        self.start(controller, "active.txt")
        wait_for(lambda: controller.snapshot()["active"] == 1)
        with self.assertRaises(ResourceBusyError) as error:
            controller.convert(Path("waiting.txt"))
        self.assertEqual(error.exception.reason, "queue_timeout")
        self.assertEqual(controller.snapshot()["pending"], 0)
        self.release.set()
        self.finish_threads()
        controller.convert(Path("later.txt"))
        self.assertEqual(controller.snapshot()["completed"], 2)
        self.assertEqual(controller.snapshot()["queue_timeouts"], 1)

    def test_independent_destinations_can_fill_the_configured_parallel_slots(self) -> None:
        entered = []
        def execute(task, config):
            entered.append(task.file_path)
            self.release.wait(5)
            return result(task)
        controller = ResourceController(ResourceConfig(max_concurrent=2), execute)
        self.start(controller, "first.txt")
        self.start(controller, "second.txt")
        wait_for(lambda: len(entered) == 2)
        self.assertEqual(controller.snapshot()["active"], 2)
        self.start(controller, "third.txt")
        wait_for(lambda: controller.snapshot()["pending"] == 1)
        self.assertEqual(len(entered), 2)

    def test_same_destination_never_uses_two_worker_slots(self) -> None:
        entered = []
        def execute(task, config):
            entered.append(task.file_path)
            if len(entered) == 1:
                self.release.wait(5)
            return result(task)
        controller = ResourceController(ResourceConfig(max_concurrent=2), execute)
        self.start(controller, "same.txt")
        wait_for(lambda: len(entered) == 1)
        self.start(controller, "same.txt")
        wait_for(lambda: controller.snapshot()["pending"] == 1)
        self.assertEqual(controller.snapshot()["active"], 1)
        self.assertEqual(len(entered), 1)
        self.release.set()
        self.finish_threads()
        self.assertEqual(len(entered), 2)
        self.assertFalse(self.errors)

    def test_failures_release_destination_and_update_timeout_counters(self) -> None:
        attempts = 0
        def execute(task, config):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConversionTimeoutError("stopped")
            if attempts == 2:
                raise WorkerHTTPError(409, "cannot write")
            return result(task)
        controller = ResourceController(ResourceConfig(), execute)
        with self.assertRaises(ConversionTimeoutError):
            controller.convert(Path("same.txt"))
        with self.assertRaises(WorkerHTTPError):
            controller.convert(Path("same.txt"))
        controller.convert(Path("same.txt"))
        snapshot = controller.snapshot()
        self.assertEqual((snapshot["failed"], snapshot["completed"], snapshot["conversion_timeouts"], snapshot["active"]), (2, 1, 1, 0))

    def test_request_options_are_serialized_without_mutating_the_caller(self) -> None:
        tasks = []
        controller = ResourceController(ResourceConfig(), lambda task, config: tasks.append(task) or result(task))
        options = ConvertOptions(output_dir=Path("original"), mineru_pages="2", mineru_tier="flash")
        controller.convert(Path("file.pdf"), engine_name="mineru", output_dir=Path("override"), options=options)
        self.assertEqual(options.output_dir, Path("original"))
        self.assertEqual(tasks[0].output_dir, "override")
        self.assertEqual(tasks[0].options["output_dir"], "override")
        self.assertEqual(tasks[0].options["mineru_pages"], "2")


class ProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = PROJECT / "tmp"
        parent.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="resource-tests-", dir=parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        temporary_patch = patch("tempfile.tempdir", str(self.root))
        temporary_patch.start()
        self.addCleanup(temporary_patch.stop)

    def test_supervisor_preserves_exit_code(self) -> None:
        code = run_supervised_process([sys.executable, "-c", "raise SystemExit(7)"], timeout_seconds=5, environment=dict(os.environ))
        self.assertEqual(code, 7)

    def test_real_subprocess_receives_thread_caps_and_temp_paths(self) -> None:
        output = self.root / "environment.json"
        names = ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "DOCS2MD_WORKER_THREADS", "TMPDIR"]
        script = "import json,os,pathlib; pathlib.Path(" + repr(str(output)) + ").write_text(json.dumps({k:os.environ[k] for k in " + repr(names) + "}))"
        code = run_supervised_process([sys.executable, "-c", script], timeout_seconds=5, environment=worker_environment(ResourceConfig(worker_threads=1), self.root))
        self.assertEqual(code, 0)
        actual = json.loads(output.read_text())
        self.assertEqual([actual[name] for name in names[:-1]], ["1"] * 4)
        self.assertEqual(actual["TMPDIR"], str(self.root))

    def test_real_markitdown_onnx_sessions_are_capped_without_changing_providers(self) -> None:
        output = self.root / "onnx-options.json"
        script = self.root / "onnx-options.py"
        script.write_text(
            "import json,pathlib,sys\n"
            f"sys.path.insert(0,{str(PROJECT / 'src')!r})\n"
            "from conversion_worker import configure_native_runtime\n"
            "configure_native_runtime(2)\n"
            "import onnxruntime as ort\n"
            "from markitdown import MarkItDown\n"
            "detector=MarkItDown()._magika\n"
            "def record(session):\n"
            "    opts=session.get_session_options()\n"
            "    return [opts.intra_op_num_threads,opts.inter_op_num_threads,session.get_providers()]\n"
            "records={'default':record(detector._onnx_session)}\n"
            "for name,count in [('large',8),('small',1)]:\n"
            "    options=ort.SessionOptions()\n"
            "    options.intra_op_num_threads=count\n"
            "    options.inter_op_num_threads=count\n"
            "    session=ort.InferenceSession(detector._model_path,sess_options=options,providers=['CPUExecutionProvider'])\n"
            "    records[name]=record(session)\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps(records))\n",
            encoding="utf-8",
        )
        code = run_supervised_process([sys.executable, str(script)], timeout_seconds=30, environment=worker_environment(ResourceConfig(), self.root))
        self.assertEqual(code, 0)
        records = json.loads(output.read_text())
        self.assertEqual(records["default"], [2, 1, ["CPUExecutionProvider"]])
        self.assertEqual(records["large"], [2, 1, ["CPUExecutionProvider"]])
        self.assertEqual(records["small"], [1, 1, ["CPUExecutionProvider"]])

    def tree_script(self, *, parent_exits: bool) -> tuple[Path, Path]:
        pid = self.root / "child.pid"
        heartbeat = self.root / "heartbeat"
        child = self.root / "child.py"
        child.write_text(
            "import pathlib,time\n"
            f"heartbeat=pathlib.Path({str(heartbeat)!r})\n"
            "while True:\n    heartbeat.write_text(str(time.time()))\n    time.sleep(.02)\n",
            encoding="utf-8",
        )
        parent = self.root / "parent.py"
        parent.write_text(
            "import pathlib,subprocess,sys,time\n"
            f"child=subprocess.Popen([sys.executable,{str(child)!r}])\n"
            f"pathlib.Path({str(pid)!r}).write_text(str(child.pid))\n"
            + ("\n" if parent_exits else "while True:\n    time.sleep(.02)\n"),
            encoding="utf-8",
        )
        return parent, pid

    def test_timeout_stops_actual_grandchild_and_releases_the_process(self) -> None:
        parent, pid_file = self.tree_script(parent_exits=False)
        started = time.monotonic()
        with self.assertRaises(ConversionTimeoutError):
            run_supervised_process([sys.executable, str(parent)], timeout_seconds=3, environment=dict(os.environ))
        self.assertLess(time.monotonic() - started, 8)
        self.assertTrue(pid_file.exists(), "The child must start before the deadline")
        wait_for(lambda: not process_alive(int(pid_file.read_text())), timeout=5)
        heartbeat = self.root / "heartbeat"
        previous = heartbeat.read_text() if heartbeat.exists() else ""
        time.sleep(.1)
        self.assertEqual(heartbeat.read_text() if heartbeat.exists() else "", previous)

    def test_successful_parent_exit_also_stops_lingering_grandchild(self) -> None:
        parent, pid_file = self.tree_script(parent_exits=True)
        code = run_supervised_process([sys.executable, str(parent)], timeout_seconds=5, environment=dict(os.environ))
        self.assertEqual(code, 0)
        self.assertTrue(pid_file.exists())
        wait_for(lambda: not process_alive(int(pid_file.read_text())), timeout=5)

    def test_actual_conversion_uses_worker_and_transports_status(self) -> None:
        source = self.root / "note.txt"
        source.write_text("Isolated worker output", encoding="utf-8")
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=30))
        status = controller.convert(source, engine_name="markitdown")
        self.assertTrue(status.success)
        self.assertEqual(status.engine, "markitdown")
        self.assertEqual(Path(status.output_path).read_text(encoding="utf-8"), "Isolated worker output")
        self.assertEqual(controller.snapshot()["completed"], 1)

    def test_actual_worker_transports_engine_client_errors(self) -> None:
        source = self.root / "note.txt"
        source.write_text("input", encoding="utf-8")
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=30))
        with self.assertRaises(WorkerHTTPError) as error:
            controller.convert(source, engine_name="missing-engine")
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Unknown engine", str(error.exception.detail))
        self.assertEqual(controller.snapshot()["failed"], 1)

    def test_large_unicode_engine_error_preserves_400_with_bounded_detail(self) -> None:
        source = self.root / "note.txt"
        source.write_text("input", encoding="utf-8")
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=30))
        with self.assertRaises(WorkerHTTPError) as error:
            controller.convert(source, engine_name="未知" * 15_000)
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Unknown engine", error.exception.detail)
        self.assertLessEqual(len(error.exception.detail), 8192)

    def test_real_llama_sdk_receives_capped_threads_and_preserves_engine_options(self) -> None:
        output = self.root / "inference-options.json"
        script = self.root / "inference-options.py"
        script.write_text(
            "import json,pathlib,sys\n"
            f"sys.path.insert(0,{str(PROJECT / 'src')!r})\n"
            "from conversion_worker import configure_llama_runtime,configure_torch_runtime\n"
            "import mineru_llama_cpp.engine as sdk\n"
            "calls=[]\n"
            "class Core:\n"
            "    eos_token_str='<end>'\n"
            "    def __init__(self,*args): calls.append(list(args))\n"
            "sdk._EngineCore=Core\n"
            "configure_llama_runtime(2)\n"
            "for requested in [None,-1,0,8,1,2]:\n"
            "    opts={} if requested is None else {'n_threads':requested}\n"
            "    engine=sdk.Engine('model.gguf','projector.gguf',n_gpu_layers=13,n_parallel=3,n_ctx_seq=256,verbosity=2,**opts)\n"
            "    engine.close()\n"
            "configure_torch_runtime(2)\n"
            "try:\n    import torch\n"
            "except (ImportError,OSError):\n    torch_options=None\n"
            "else:\n    torch_options=[torch.get_num_threads(),torch.get_num_interop_threads()]\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps({{'calls':calls,'torch':torch_options}}))\n",
            encoding="utf-8",
        )
        code = run_supervised_process([sys.executable, str(script)], timeout_seconds=30, environment=worker_environment(ResourceConfig(), self.root))
        self.assertEqual(code, 0)
        recorded = json.loads(output.read_text())
        calls = recorded["calls"]
        self.assertEqual([call[-1] for call in calls], [2, 2, 2, 2, 1, 2])
        for call in calls:
            self.assertEqual(call[:-1], ["model.gguf", "projector.gguf", 256, 13, 3, 2])
        if recorded["torch"] is not None:
            self.assertLessEqual(recorded["torch"][0], 2)
            self.assertEqual(recorded["torch"][1], 1)

    def test_actual_mineru_cli_runs_through_isolated_bootstrap_without_fallback(self) -> None:
        from test_real_documents import isolated_environment
        source = PROJECT / "tests" / "fixtures" / "real_documents" / "inventory.csv"
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=60))
        environment = isolated_environment(self.root)
        with patch.dict(os.environ, environment, clear=True):
            status = controller.convert(source, engine_name="mineru", output_dir=self.root / "converted", options=ConvertOptions(mineru_tier="flash"))
        self.assertEqual(status.engine, "mineru")
        self.assertFalse(status.fallback)
        text = Path(status.output_path).read_text(encoding="utf-8")
        self.assertIn("Apricot", text)
        self.assertIn("中文回归", text)

    def test_actual_worker_transports_output_conflicts(self) -> None:
        source = self.root / "note.txt"
        source.write_text("input", encoding="utf-8")
        target = self.root / "not-a-directory"
        target.write_text("existing file", encoding="utf-8")
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=30))
        with self.assertRaises(WorkerHTTPError) as error:
            controller.convert(source, engine_name="markitdown", output_dir=target)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("Unable to write", str(error.exception.detail))
        self.assertEqual(target.read_text(encoding="utf-8"), "existing file")

    def test_timed_out_worker_only_writes_staging_and_preserves_final_output(self) -> None:
        source = self.root / "source.txt"
        source.write_text("input", encoding="utf-8")
        final = self.root / "source.txt.docs2md"
        final.mkdir()
        (final / "source.md").write_text("previous ![](images/old.png)", encoding="utf-8")
        (final / "images").mkdir()
        (final / "images" / "old.png").write_bytes(b"previous image")
        ready = self.root / "staging-ready"
        fake_worker = self.root / "slow_worker.py"
        fake_worker.write_text(
            "import json,pathlib,sys,time\n"
            "task=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
            "base=pathlib.Path(task['output_dir'])\n"
            "assert task['options']['output_dir']==str(base)\n"
            "document=base/'source.txt.docs2md'\n"
            "document.mkdir(parents=True)\n"
            "(document/'source.md').write_text('incomplete ![](images/new.png)')\n"
            "(document/'images').mkdir()\n"
            "(document/'images'/'new.png').write_bytes(b'incomplete image')\n"
            f"pathlib.Path({str(ready)!r}).write_text(str(base))\n"
            "while True:\n    time.sleep(.02)\n",
            encoding="utf-8",
        )
        supervise = resource_control.run_supervised_process
        def use_slow_worker(command, **kwargs):
            return supervise([command[0], str(fake_worker), *command[2:]], **kwargs)
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=3))
        with patch("resource_control.run_supervised_process", side_effect=use_slow_worker):
            with self.assertRaises(ConversionTimeoutError):
                controller.convert(source, engine_name="markitdown")
        self.assertTrue(ready.exists(), "The isolated worker must write its partial result")
        staged = Path(ready.read_text())
        self.assertFalse(staged.parent.exists(), "The supervisor must remove the entire job directory")
        self.assertEqual((final / "source.md").read_text(), "previous ![](images/old.png)")
        self.assertEqual((final / "images" / "old.png").read_bytes(), b"previous image")
        self.assertFalse((final / "images" / "new.png").exists())
        self.assertEqual(controller.snapshot()["active"], 0)

    def completed_image_worker(self) -> tuple[Path, Path]:
        source = self.root / "source.txt"
        source.write_text("input", encoding="utf-8")
        worker = self.root / "image_worker.py"
        worker.write_text(
            "import json,pathlib,sys\n"
            "task=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
            "base=pathlib.Path(task['output_dir'])\n"
            "document=base/'source.txt.docs2md'\n"
            "document.mkdir(parents=True)\n"
            "markdown=document/'source.md'\n"
            "markdown.write_text('new ![](images/new.png)')\n"
            "(document/'images').mkdir()\n"
            "(document/'images'/'new.png').write_bytes(b'new image')\n"
            "response={'kind':'result','data':{'success':True,'engine':'mineru',"
            "'output_path':str(markdown),'output_dir':str(base),'images_dir':str(document/'images'),"
            "'fallback':False,'fallback_from':None,'message':'staged'}}\n"
            "pathlib.Path(sys.argv[2]).write_text(json.dumps(response))\n",
            encoding="utf-8",
        )
        return source, worker

    def test_parent_publishes_images_and_rebases_all_response_paths(self) -> None:
        source, worker = self.completed_image_worker()
        supervise = resource_control.run_supervised_process
        job_directories = []
        def use_image_worker(command, **kwargs):
            job_directories.append(Path(command[2]).parent)
            return supervise([command[0], str(worker), *command[2:]], **kwargs)
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=5))
        with patch("resource_control.run_supervised_process", side_effect=use_image_worker):
            status = controller.convert(source, engine_name="mineru")
        final = self.root / "source.txt.docs2md"
        self.assertEqual(status.output_path, str(final / "source.md"))
        self.assertEqual(status.output_dir, str(self.root))
        self.assertEqual(status.images_dir, str(final / "images"))
        self.assertEqual(status.message, f"Saved to {final / 'source.md'}")
        self.assertEqual((final / "source.md").read_text(), "new ![](images/new.png)")
        self.assertEqual((final / "images" / "new.png").read_bytes(), b"new image")
        self.assertTrue(all(not path.exists() for path in job_directories))

    def test_parent_publication_failure_restores_previous_images_and_http_conflict(self) -> None:
        source, worker = self.completed_image_worker()
        final = self.root / "source.txt.docs2md"
        final.mkdir()
        (final / "source.md").write_text("previous ![](images/old.png)")
        (final / "images").mkdir()
        (final / "images" / "old.png").write_bytes(b"previous image")
        supervise = resource_control.run_supervised_process
        def use_image_worker(command, **kwargs):
            return supervise([command[0], str(worker), *command[2:]], **kwargs)
        controller = ResourceController(ResourceConfig(conversion_timeout_seconds=5))
        with patch("resource_control.run_supervised_process", side_effect=use_image_worker), patch(
            "engines.mineru.write_text_output", side_effect=OutputWriteError(final / "source.md", PermissionError("locked")),
        ):
            with self.assertRaises(WorkerHTTPError) as error:
                controller.convert(source, engine_name="mineru")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual((final / "source.md").read_text(), "previous ![](images/old.png)")
        self.assertEqual((final / "images" / "old.png").read_bytes(), b"previous image")
        self.assertFalse((final / "images" / "new.png").exists())
        self.assertEqual(controller.snapshot()["active"], 0)


if __name__ == "__main__":
    unittest.main()
