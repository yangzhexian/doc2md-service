"""Persist bounded batch jobs and dispatch one file at a time fairly."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException

from batch_inputs import BatchInputPlan
from engines.base import ConvertOptions, ConvertStatusResponse

ACTIVE_STATES = ("queued", "running", "cancelling")
TERMINAL_STATES = ("completed", "completed_with_errors", "cancelled")
ERROR_LIMIT = 2048
BUSY_RETRY_LIMIT = 3


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _detail(value: object) -> str:
    return str(value)[:ERROR_LIMIT]


def _key(value: str | None) -> str | None:
    if value is not None and (
        not isinstance(value, str) or not value.strip() or len(value) > 128 or not value.isascii()
    ):
        raise HTTPException(400, "Idempotency key must be nonempty ASCII text of at most 128 characters")
    return value


def _canonical(identity: dict) -> str:
    if not isinstance(identity, dict):
        raise HTTPException(400, "Request identity must be a JSON object")
    try:
        return json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Request identity must contain JSON-compatible values") from exc


class BatchJobManager:
    """One SQLite owner and one cooperative dispatcher; construction does no IO."""

    def __init__(
        self,
        state_dir: Path,
        converter: Callable[..., ConvertStatusResponse],
        *,
        max_active_jobs: int = 5,
        max_history_jobs: int = 100,
        select_engine: Callable[[Path], str | None] | None = None,
    ) -> None:
        for name, value in (("max_active_jobs", max_active_jobs), ("max_history_jobs", max_history_jobs)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise HTTPException(400, f"{name} must be a positive integer")
        if max_history_jobs < max_active_jobs:
            raise HTTPException(400, "max_history_jobs must be at least max_active_jobs")
        self.state_dir = Path(state_dir)
        self.max_active_jobs = max_active_jobs
        self.max_history_jobs = max_history_jobs
        self._converter = converter
        self._select_engine = select_engine
        self._condition = threading.Condition(threading.RLock())
        self._lifecycle = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._owner = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._last_job: str | None = None
        self._scheduler_error: str | None = None

    def _acquire_owner(self) -> None:
        owner = (self.state_dir / "owner.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                owner.seek(0, os.SEEK_END)
                if owner.tell() == 0:
                    owner.write(b"0")
                    owner.flush()
                owner.seek(0)
                msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            owner.close()
            raise HTTPException(409, "Another batch manager already owns this state directory") from exc
        self._owner = owner

    def _release_owner(self) -> None:
        owner, self._owner = self._owner, None
        if owner is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                owner.seek(0)
                msvcrt.locking(owner.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
        finally:
            owner.close()
        # Keep the lock file: unlinking it can let another process lock a new
        # inode while an existing manager still owns the original file.

    def start(self) -> None:
        with self._lifecycle, self._condition:
            if self._connection is not None:
                return
            self.state_dir = self.state_dir.expanduser().resolve()
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                self._acquire_owner()
                connection = sqlite3.connect(self.state_dir / "jobs.sqlite3", timeout=5, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                self._connection = connection
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise HTTPException(503, "Unsupported batch database version")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        folder TEXT NOT NULL,
                        output_dir TEXT NOT NULL,
                        recursive INTEGER NOT NULL,
                        options_json TEXT NOT NULL,
                        idempotency_key TEXT UNIQUE,
                        identity_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        cancel_requested INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS items (
                        job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                        ordinal INTEGER NOT NULL,
                        file TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        output_dir TEXT NOT NULL,
                        engine_name TEXT,
                        size_bytes INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        busy_retries INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at REAL NOT NULL DEFAULT 0,
                        status_code INTEGER,
                        detail TEXT,
                        result_json TEXT,
                        PRIMARY KEY (job_id, ordinal)
                    );
                    CREATE INDEX IF NOT EXISTS items_ready ON items(job_id, status, next_attempt_at, ordinal);
                    PRAGMA user_version=1;
                """)
                self._recover()
                self._stopping = False
                self._last_job = None
                self._scheduler_error = None
                self._thread = threading.Thread(target=self._dispatch, name="docs2md-batches", daemon=True)
                self._thread.start()
            except BaseException:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                self._release_owner()
                raise

    def close(self) -> None:
        with self._lifecycle:
            with self._condition:
                if self._connection is None:
                    return
                self._stopping = True
                self._condition.notify_all()
                thread = self._thread
            if thread is not None:
                thread.join()
            with self._condition:
                self._thread = None
                self._connection.close()
                self._connection = None
                self._release_owner()

    def _db(self, *, write: bool = False) -> sqlite3.Connection:
        if self._connection is None or (write and (self._stopping or self._scheduler_error is not None)):
            raise HTTPException(503, "Batch manager is not accepting work")
        return self._connection

    def _recover(self) -> None:
        connection = self._db()
        with connection:
            connection.execute(
                "UPDATE items SET status='interrupted',status_code=503,detail=?,next_attempt_at=0 WHERE status='running'",
                ("Service stopped while this file was running; its output may exist. Retry explicitly to reconcile it.",),
            )
            for job in connection.execute("SELECT job_id,status,cancel_requested FROM jobs").fetchall():
                if job["cancel_requested"]:
                    connection.execute(
                        "UPDATE items SET status='cancelled',next_attempt_at=0,status_code=NULL,detail=? WHERE job_id=? AND status='pending'",
                        ("Cancelled before conversion", job["job_id"]),
                    )
                if job["status"] in ACTIVE_STATES or job["cancel_requested"]:
                    self._refresh(job["job_id"])

    def _find(self, job_id: str) -> sqlite3.Row:
        row = self._db().execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Batch job not found")
        return row

    def _counts(self, job_id: str) -> dict:
        counts = {row["status"]: row["amount"] for row in self._db().execute(
            "SELECT status,COUNT(*) AS amount FROM items WHERE job_id=? GROUP BY status", (job_id,),
        )}
        result = {
            "total": sum(counts.values()),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0) + counts.get("interrupted", 0),
            "cancelled": counts.get("cancelled", 0),
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
        }
        result["processed"] = result["succeeded"] + result["failed"] + result["cancelled"]
        return result

    def _view(self, job_id: str, *, items: bool = True) -> dict:
        row = self._find(job_id)
        result = {
            "job_id": row["job_id"], "status": row["status"], "folder": row["folder"],
            "output_dir": row["output_dir"], "recursive": bool(row["recursive"]),
            "options": json.loads(row["options_json"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"], "cancel_requested": bool(row["cancel_requested"]),
            **self._counts(job_id),
        }
        if items:
            result["items"] = [{
                "index": item["ordinal"], "file": item["file"], "relative_path": item["relative_path"],
                "output_dir": item["output_dir"], "status": item["status"], "attempts": item["attempts"],
                "status_code": item["status_code"], "detail": item["detail"],
                "result": json.loads(item["result_json"]) if item["result_json"] is not None else None,
            } for item in self._db().execute("SELECT * FROM items WHERE job_id=? ORDER BY ordinal", (job_id,))]
        return result

    def lookup_idempotent(self, key: str, request_identity: dict) -> dict | None:
        key = _key(key)
        signature = _canonical(request_identity)
        with self._condition:
            return self._lookup(key, signature)

    def _lookup(self, key: str | None, signature: str) -> dict | None:
        if key is None:
            return None
        row = self._db().execute("SELECT job_id,identity_json FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["identity_json"] != signature:
            raise HTTPException(409, "Idempotency key is already associated with a different request")
        return self._view(row["job_id"])

    def _active_count(self) -> int:
        return self._db().execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]

    def _make_history_room(self) -> None:
        connection = self._db()
        total = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        while total >= self.max_history_jobs:
            oldest = connection.execute(
                "SELECT job_id FROM jobs WHERE status IN ('completed','completed_with_errors','cancelled') ORDER BY sequence LIMIT 1",
            ).fetchone()
            if oldest is None:
                raise HTTPException(429, "Batch history is full of unfinished work; cancel an older paused job before submitting more", headers={"Retry-After": "1"})
            connection.execute("DELETE FROM jobs WHERE job_id=?", (oldest["job_id"],))
            total -= 1

    def submit(
        self,
        plan: BatchInputPlan,
        *,
        engine_name: str | None = None,
        options: ConvertOptions,
        idempotency_key: str | None = None,
        request_identity: dict | None = None,
    ) -> dict:
        key = _key(idempotency_key)
        engine_name = engine_name.strip().lower() if engine_name is not None else None
        public_options = {"engine": engine_name, "tier": options.mineru_tier, "remote": options.mineru_remote, "pages": options.mineru_pages}
        identity = request_identity if request_identity is not None else {
            "folder_path": str(plan.folder), "output_dir": str(plan.output_dir), "recursive": plan.recursive,
            "extensions": None, **public_options,
        }
        signature = _canonical(identity)
        with self._condition:
            self._db(write=True)
            existing = self._lookup(key, signature)
            if existing is not None:
                return existing
        # Freeze selection before scheduling, without holding the database
        # lock while calling application code. The input plan is already fixed.
        entries = [(item, engine_name if engine_name is not None else self._select_engine(item.path) if self._select_engine else None) for item in plan.items]
        with self._condition:
            connection = self._db(write=True)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._lookup(key, signature)
                if existing is not None:
                    return existing
                if entries and self._active_count() >= self.max_active_jobs:
                    raise HTTPException(429, "Too many active batch jobs", headers={"Retry-After": "1"})
                self._make_history_room()
                job_id = uuid4().hex
                now = _timestamp()
                connection.execute(
                    "INSERT INTO jobs(job_id,status,folder,output_dir,recursive,options_json,idempotency_key,identity_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (job_id, "queued" if entries else "completed", str(plan.folder), str(plan.output_dir), int(plan.recursive),
                     _canonical(public_options), key, signature, now, now),
                )
                connection.executemany(
                    "INSERT INTO items(job_id,ordinal,file,relative_path,output_dir,engine_name,size_bytes) VALUES(?,?,?,?,?,?,?)",
                    [(job_id, index, str(item.path), item.relative_path.as_posix(), str(item.output_dir), chosen, item.size_bytes)
                     for index, (item, chosen) in enumerate(entries)],
                )
            self._condition.notify_all()
            return self._view(job_id)

    def get(self, job_id: str) -> dict:
        with self._condition:
            return self._view(job_id)

    def list_jobs(self, limit: int = 20) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be an integer between 1 and 100")
        with self._condition:
            rows = self._db().execute("SELECT job_id FROM jobs ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
            return [self._view(row["job_id"], items=False) for row in rows]

    def _refresh(self, job_id: str) -> None:
        job = self._find(job_id)
        counts = self._counts(job_id)
        if job["cancel_requested"]:
            status = "cancelling" if counts["running"] else "cancelled"
        elif counts["running"]:
            status = "running"
        elif counts["pending"]:
            status = "paused" if job["status"] == "paused" else "queued"
        elif counts["cancelled"]:
            status = "cancelled"
        elif counts["failed"]:
            status = "completed_with_errors"
        else:
            status = "completed"
        self._db().execute("UPDATE jobs SET status=?,updated_at=? WHERE job_id=?", (status, _timestamp(), job_id))

    def cancel(self, job_id: str) -> dict:
        with self._condition:
            connection = self._db(write=True)
            job = self._find(job_id)
            if job["status"] in TERMINAL_STATES:
                return self._view(job_id)
            with connection:
                connection.execute("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
                connection.execute(
                    "UPDATE items SET status='cancelled',next_attempt_at=0,status_code=NULL,detail=? WHERE job_id=? AND status='pending'",
                    ("Cancelled before conversion", job_id),
                )
                self._refresh(job_id)
            self._condition.notify_all()
            return self._view(job_id)

    def retry(self, job_id: str, include_cancelled: bool = False) -> dict:
        if not isinstance(include_cancelled, bool):
            raise HTTPException(400, "include_cancelled must be a boolean")
        with self._condition:
            connection = self._db(write=True)
            job = self._find(job_id)
            if job["status"] not in (*TERMINAL_STATES, "paused"):
                raise HTTPException(409, "Only terminal or paused batch jobs can be retried")
            retry_states = "'failed','interrupted','pending'" + (",'cancelled'" if include_cancelled else "")
            available = connection.execute(f"SELECT COUNT(*) FROM items WHERE job_id=? AND status IN ({retry_states})", (job_id,)).fetchone()[0]
            if not available:
                raise HTTPException(409, "Batch job has no eligible files to retry")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if self._active_count() >= self.max_active_jobs:
                    raise HTTPException(429, "Too many active batch jobs", headers={"Retry-After": "1"})
                connection.execute(
                    f"UPDATE items SET status='pending',status_code=NULL,detail=NULL,result_json=NULL,busy_retries=0,next_attempt_at=0 WHERE job_id=? AND status IN ({retry_states})",
                    (job_id,),
                )
                connection.execute("UPDATE jobs SET status='queued',cancel_requested=0,updated_at=? WHERE job_id=?", (_timestamp(), job_id))
            self._condition.notify_all()
            return self._view(job_id)

    def snapshot(self) -> dict:
        with self._condition:
            counts = {} if self._connection is None else {
                row["status"]: row["amount"] for row in self._connection.execute("SELECT status,COUNT(*) AS amount FROM jobs GROUP BY status")
            }
            return {
                "limits": {"max_active_jobs": self.max_active_jobs, "max_history_jobs": self.max_history_jobs},
                "active": sum(counts.get(status, 0) for status in ACTIVE_STATES), "total": sum(counts.values()),
                **{status: counts.get(status, 0) for status in (*ACTIVE_STATES, *TERMINAL_STATES, "paused")},
                "scheduler_running": self._thread is not None and self._thread.is_alive(),
                "scheduler_error": self._scheduler_error,
            }

    def _claim(self) -> tuple[sqlite3.Row, sqlite3.Row] | None:
        connection = self._db()
        jobs = connection.execute("SELECT * FROM jobs WHERE status IN ('queued','running') AND cancel_requested=0 ORDER BY sequence").fetchall()
        ids = [job["job_id"] for job in jobs]
        if self._last_job in ids:
            start = ids.index(self._last_job) + 1
            jobs = jobs[start:] + jobs[:start]
        with connection:
            for job in jobs:
                item = connection.execute(
                    "SELECT * FROM items WHERE job_id=? AND status='pending' AND next_attempt_at<=? ORDER BY ordinal LIMIT 1",
                    (job["job_id"], time.time()),
                ).fetchone()
                if item is None:
                    continue
                connection.execute("UPDATE items SET status='running',attempts=attempts+1 WHERE job_id=? AND ordinal=?", (job["job_id"], item["ordinal"]))
                connection.execute("UPDATE jobs SET status='running',updated_at=? WHERE job_id=?", (_timestamp(), job["job_id"]))
                self._last_job = job["job_id"]
                return job, item
        return None

    def _finish(self, job_id: str, index: int, result: dict | None, error: HTTPException | None) -> None:
        connection = self._db()
        with connection:
            job = self._find(job_id)
            if error is not None and error.status_code == 429:
                item = connection.execute("SELECT busy_retries FROM items WHERE job_id=? AND ordinal=?", (job_id, index)).fetchone()
                busy = item["busy_retries"] + 1
                try:
                    delay = float((error.headers or {}).get("Retry-After", "1"))
                except (TypeError, ValueError):
                    delay = 1
                delay = min(delay, 60) if math.isfinite(delay) and delay >= 0 else 1
                connection.execute(
                    "UPDATE items SET status=?,attempts=MAX(attempts-1,0),busy_retries=?,next_attempt_at=?,status_code=?,detail=? WHERE job_id=? AND ordinal=?",
                    ("cancelled" if job["cancel_requested"] else "pending", busy, 0 if job["cancel_requested"] else time.time() + delay,
                     None if job["cancel_requested"] else 429, "Cancelled before retry" if job["cancel_requested"] else _detail(error.detail), job_id, index),
                )
                if busy >= BUSY_RETRY_LIMIT and not job["cancel_requested"]:
                    connection.execute("UPDATE jobs SET status='paused' WHERE job_id=?", (job_id,))
            elif error is not None:
                connection.execute(
                    "UPDATE items SET status='failed',status_code=?,detail=?,result_json=NULL,next_attempt_at=0 WHERE job_id=? AND ordinal=?",
                    (error.status_code, _detail(error.detail), job_id, index),
                )
            else:
                connection.execute(
                    "UPDATE items SET status='succeeded',status_code=NULL,detail=NULL,result_json=?,busy_retries=0,next_attempt_at=0 WHERE job_id=? AND ordinal=?",
                    (json.dumps(result, ensure_ascii=False), job_id, index),
                )
            self._refresh(job_id)

    def _dispatch(self) -> None:
        try:
            self._dispatch_loop()
        except Exception as exc:
            with self._condition:
                self._scheduler_failed(exc)

    def _scheduler_failed(self, exc: Exception) -> None:
        # Keep uncertain running records intact. Recovery will mark them
        # interrupted rather than pretending that publication did not happen.
        self._scheduler_error = _detail(f"{type(exc).__name__}: {exc}")
        self._condition.notify_all()

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping or self._scheduler_error is not None:
                    return
                try:
                    claimed = self._claim()
                except Exception as exc:
                    self._scheduler_failed(exc)
                    return
                if claimed is None:
                    self._condition.wait(0.25)
                    continue
            job, item = claimed
            result = None
            error = None
            try:
                options = json.loads(job["options_json"])
                converted = self._converter(
                    Path(item["file"]), engine_name=item["engine_name"], output_dir=Path(item["output_dir"]),
                    options=ConvertOptions.from_request(output_dir=Path(item["output_dir"]), tier=options["tier"], remote=options["remote"], pages=options["pages"]),
                )
                if not converted.success:
                    raise HTTPException(500, converted.message or "Conversion did not succeed")
                result = asdict(converted)
                if isinstance(result.get("message"), str):
                    result["message"] = result["message"][:ERROR_LIMIT]
            except HTTPException as exc:
                error = exc
            except Exception as exc:
                error = HTTPException(500, _detail(exc))
            with self._condition:
                try:
                    self._finish(job["job_id"], item["ordinal"], result, error)
                except Exception as exc:
                    self._scheduler_failed(exc)
                    return
                self._condition.notify_all()
