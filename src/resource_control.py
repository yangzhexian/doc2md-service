"""Bound conversion admission and supervise isolated conversion processes."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from engines.base import (
    ConvertOptions, ConvertStatusResponse, OutputWriteError, resolve_output_path, write_text_output,
)


class ResourceBusyError(RuntimeError):
    """A bounded pending queue cannot admit or start this conversion."""

    def __init__(self, reason: str, retry_after: int) -> None:
        self.reason = reason
        self.retry_after = retry_after
        message = (
            "Conversion queue is full; retry later"
            if reason == "queue_full"
            else "Conversion queue wait timed out; retry later"
        )
        super().__init__(message)


class ConversionTimeoutError(RuntimeError):
    """A conversion exceeded its deadline and its process tree was stopped."""


class WorkerExecutionError(RuntimeError):
    """A worker exited or produced an invalid result."""


class WorkerHTTPError(RuntimeError):
    """Carry an engine's HTTP error across the worker boundary."""

    def __init__(self, status_code: int, detail: object, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(str(detail))


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer >= {minimum}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


@dataclass(frozen=True)
class ResourceConfig:
    """One validated configuration shared by admission and worker execution."""

    max_concurrent: int = 1
    max_pending: int = 4
    queue_timeout_seconds: float = 30.0
    conversion_timeout_seconds: float = 1800.0
    worker_threads: int = 2

    def __post_init__(self) -> None:
        for name, minimum in (("max_concurrent", 1), ("max_pending", 0), ("worker_threads", 1)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        for name in ("queue_timeout_seconds", "conversion_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ResourceConfig:
        values = os.environ if env is None else env
        return cls(
            max_concurrent=_integer(values, "DOCS2MD_MAX_CONCURRENT_CONVERSIONS", 1),
            max_pending=_integer(values, "DOCS2MD_MAX_PENDING_CONVERSIONS", 4, minimum=0),
            queue_timeout_seconds=_seconds(values, "DOCS2MD_QUEUE_TIMEOUT_SECONDS", 30.0),
            conversion_timeout_seconds=_seconds(values, "DOCS2MD_CONVERSION_TIMEOUT_SECONDS", 1800.0),
            worker_threads=_integer(values, "DOCS2MD_WORKER_THREADS", 2),
        )


@dataclass(frozen=True)
class ConversionTask:
    file_path: str
    engine_name: str | None
    output_dir: str | None
    options: dict


Executor = Callable[[ConversionTask, ResourceConfig], ConvertStatusResponse]


class ResourceController:
    """Admit in FIFO order, sharing both process slots and destination keys."""

    def __init__(self, config: ResourceConfig, executor: Executor | None = None) -> None:
        self.config = config
        self._executor = executor or subprocess_worker
        self._condition = threading.Condition()
        self._waiting: deque[object] = deque()
        self._active = 0
        self._active_keys: set[str] = set()
        self._completed = 0
        self._failed = 0
        self._rejected = 0
        self._conversion_timeouts = 0
        self._queue_timeouts = 0

    def snapshot(self) -> dict:
        with self._condition:
            return {
                "limits": asdict(self.config),
                "active": self._active,
                "pending": len(self._waiting),
                "completed": self._completed,
                "failed": self._failed,
                "rejected": self._rejected,
                "conversion_timeouts": self._conversion_timeouts,
                "queue_timeouts": self._queue_timeouts,
            }

    def _enter(self, key: str) -> None:
        deadline = time.monotonic() + self.config.queue_timeout_seconds
        retry_after = max(1, math.ceil(self.config.queue_timeout_seconds))
        ticket = object()
        with self._condition:
            if not self._waiting and self._active < self.config.max_concurrent and key not in self._active_keys:
                self._active += 1
                self._active_keys.add(key)
                return
            if len(self._waiting) >= self.config.max_pending:
                self._rejected += 1
                raise ResourceBusyError("queue_full", retry_after)
            self._waiting.append(ticket)
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._rejected += 1
                        self._queue_timeouts += 1
                        raise ResourceBusyError("queue_timeout", retry_after)
                    if self._waiting[0] is ticket and self._active < self.config.max_concurrent and key not in self._active_keys:
                        self._waiting.popleft()
                        self._active += 1
                        self._active_keys.add(key)
                        self._condition.notify_all()
                        return
                    self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            finally:
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()

    def convert(
        self,
        file_path: Path,
        *,
        engine_name: str | None = None,
        output_dir: Path | None = None,
        options: ConvertOptions | None = None,
        output_key: str | None = None,
    ) -> ConvertStatusResponse:
        opts = options or ConvertOptions(output_dir=output_dir)
        target_dir = output_dir if output_dir is not None else opts.output_dir
        if output_key is None:
            output_key = str(resolve_output_path(file_path, target_dir)[1].parent)
        key = os.path.normcase(os.path.abspath(output_key))
        serialized_options = asdict(opts)
        serialized_options["output_dir"] = str(target_dir) if target_dir is not None else None
        task = ConversionTask(
            file_path=str(file_path),
            engine_name=engine_name,
            output_dir=str(target_dir) if target_dir is not None else None,
            options=serialized_options,
        )
        self._enter(key)
        try:
            result = self._executor(task, self.config)
        except BaseException as exc:
            with self._condition:
                self._failed += 1
                if isinstance(exc, ConversionTimeoutError):
                    self._conversion_timeouts += 1
            raise
        else:
            with self._condition:
                self._completed += 1
            return result
        finally:
            # subprocess_worker returns only after its worker tree is stopped.
            with self._condition:
                self._active -= 1
                self._active_keys.remove(key)
                self._condition.notify_all()


def worker_environment(config: ResourceConfig, temporary: Path) -> dict[str, str]:
    environment = dict(os.environ)
    count = str(config.worker_threads)
    for name in (
        "OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
    ):
        environment[name] = count
    environment.update({
        "TOKENIZERS_PARALLELISM": "false",
        "DOCS2MD_WORKER_THREADS": count,
        "DOCS2MD_ISOLATED_WORKER": "1",
        "MINERU_INTRA_OP_NUM_THREADS": count,
        "MINERU_INTER_OP_NUM_THREADS": "1",
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "TMPDIR": str(temporary),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return environment


class _WindowsJob:
    """A kill-on-close JobObject assigned before the suspended worker starts."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        api = self._api
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            code = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(code)

    def start(self, process: subprocess.Popen) -> None:
        ctypes = self._ctypes
        from ctypes import wintypes

        api = self._api
        if not api.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

        class THREAD_ENTRY(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ]

        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREAD_ENTRY)]
        api.Thread32First.restype = wintypes.BOOL
        api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREAD_ENTRY)]
        api.Thread32Next.restype = wintypes.BOOL
        api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenThread.restype = wintypes.HANDLE
        api.ResumeThread.argtypes = [wintypes.HANDLE]
        api.ResumeThread.restype = wintypes.DWORD
        snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = THREAD_ENTRY()
            entry.dwSize = ctypes.sizeof(entry)
            found = api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    handle = api.OpenThread(0x2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if api.ResumeThread(handle) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        api.CloseHandle(handle)
                    return
                found = api.Thread32Next(snapshot, ctypes.byref(entry))
            raise WorkerExecutionError("Unable to resume the conversion worker")
        finally:
            api.CloseHandle(snapshot)

    def close(self) -> None:
        if self._handle:
            self._api.CloseHandle(self._handle)
            self._handle = None

    def finish(self) -> None:
        """Wait for all assigned descendants to stop before closing the job."""
        ctypes = self._ctypes
        from ctypes import wintypes

        class ACCOUNTING(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_int64) for name in (
                    "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime",
                )
            ] + [
                (name, wintypes.DWORD) for name in (
                    "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses",
                )
            ]

        api = self._api
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        try:
            if not api.TerminateJobObject(self._handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + 5
            while True:
                accounting = ACCOUNTING()
                if not api.QueryInformationJobObject(self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    raise WorkerExecutionError("Conversion worker descendants did not stop after termination")
                time.sleep(.01)
        finally:
            self.close()


def _kill_process_tree(process: subprocess.Popen, job: _WindowsJob | None) -> None:
    try:
        if job is not None:
            job.finish()
        elif os.name == "nt":
            # Used only if setup failed before the worker joined its JobObject.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def run_supervised_process(
    command: list[str], *, timeout_seconds: float, environment: dict[str, str],
) -> int:
    """Reap the process and stop all descendants before releasing its slot."""
    job: _WindowsJob | None = _WindowsJob() if os.name == "nt" else None
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=0x4 | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        if job is not None:
            job.start(process)
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConversionTimeoutError(
                    f"Conversion exceeded {timeout_seconds:g} seconds; its worker process tree was stopped"
                )
            try:
                return process.wait(timeout=min(remaining, 60))
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process is not None:
            _kill_process_tree(process, job)
        elif job is not None:
            job.close()


def subprocess_worker(task: ConversionTask, config: ResourceConfig) -> ConvertStatusResponse:
    """Run one conversion, including engine fallback, outside the API process."""
    with tempfile.TemporaryDirectory(prefix="docs2md_worker_") as directory:
        root = Path(directory).resolve()
        temporary = root / "tmp"
        temporary.mkdir()
        request_path = root / "request.json"
        response_path = root / "response.json"
        staging_output = root / "output"
        staged_task = replace(
            task,
            output_dir=str(staging_output),
            options={**task.options, "output_dir": str(staging_output)},
        )
        request_path.write_text(json.dumps(asdict(staged_task), ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, str(Path(__file__).with_name("conversion_worker.py")), str(request_path), str(response_path)]
        try:
            code = run_supervised_process(
                command, timeout_seconds=config.conversion_timeout_seconds,
                environment=worker_environment(config, temporary),
            )
        except OSError as exc:
            raise WorkerExecutionError(f"Unable to start the conversion worker: {exc}") from exc
        if code != 0:
            raise WorkerExecutionError(f"Conversion worker exited unexpectedly (exit code {code})")
        try:
            if response_path.stat().st_size > 64 * 1024:
                raise ValueError("worker response exceeds its limit")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if response["kind"] == "http_error":
                status_code = response["status_code"]
                if isinstance(status_code, bool) or not isinstance(status_code, int) or not 400 <= status_code <= 599:
                    raise ValueError("invalid worker error status")
                raise WorkerHTTPError(status_code, response["detail"], response.get("headers"))
            if response["kind"] != "result":
                raise ValueError("unknown worker response kind")
            status = ConvertStatusResponse(**response["data"])
            return _publish_result(task, status, staging_output)
        except WorkerHTTPError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise WorkerExecutionError(f"Conversion worker produced an invalid response: {exc}") from exc


def _publish_result(task: ConversionTask, status: ConvertStatusResponse, staging: Path) -> ConvertStatusResponse:
    """Publish only after the worker tree exits, keeping deadlines away from final output."""
    source = Path(task.file_path)
    expected_staged = resolve_output_path(source, staging)[1].resolve()
    if not status.success or Path(status.output_path).resolve() != expected_staged:
        raise ValueError("worker returned an unexpected staged output path")
    images = Path(status.images_dir).resolve() if status.images_dir else None
    if images is not None and images != expected_staged.parent / "images":
        raise ValueError("worker returned an unexpected staged images path")
    target = Path(task.output_dir) if task.output_dir is not None else None
    output_dir, output_path = resolve_output_path(source, target)
    text = expected_staged.read_text(encoding="utf-8")
    try:
        if status.engine == "mineru" or images is not None:
            from engines.mineru import _save_markdown
            _save_markdown(
                text, output_dir, source.stem, images,
                directory_name=output_path.parent.name,
            )
        else:
            write_text_output(output_path, text)
    except OutputWriteError as exc:
        raise WorkerHTTPError(409, str(exc)) from exc
    return replace(
        status,
        output_path=str(output_path.resolve()),
        output_dir=str(output_dir.resolve()),
        images_dir=str((output_path.parent / "images").resolve()) if images is not None else None,
        message=f"Saved to {output_path.resolve()}",
    )
