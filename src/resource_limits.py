"""Bound request bodies, uploaded files, and folder preflight work."""

from __future__ import annotations

import os
import re
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Mapping

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MIB = 1024 * 1024
UPLOAD_CHUNK_BYTES = MIB
UPLOAD_OVERHEAD_BYTES = MIB


def _positive_integer(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None:
        return default
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


@dataclass(frozen=True)
class ResourceLimits:
    """Validated limits; one snapshot is shared by middleware and endpoints."""

    max_file_bytes: int = 100 * MIB
    max_batch_files: int = 100
    max_folder_entries: int = 10_000
    max_json_body_bytes: int = field(default=64 * 1024, init=False)

    def __post_init__(self) -> None:
        for name in ("max_file_bytes", "max_batch_files", "max_folder_entries"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def max_upload_body_bytes(self) -> int:
        """Allow multipart headers and fields in addition to the document."""
        return self.max_file_bytes + UPLOAD_OVERHEAD_BYTES

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ResourceLimits:
        values = os.environ if env is None else env
        return cls(
            max_file_bytes=_positive_integer(values, "DOCS2MD_MAX_FILE_BYTES", 100 * MIB),
            max_batch_files=_positive_integer(values, "DOCS2MD_MAX_BATCH_FILES", 100),
            max_folder_entries=_positive_integer(values, "DOCS2MD_MAX_FOLDER_ENTRIES", 10_000),
        )


class _RequestBodyTooLarge(Exception):
    """Interrupt request parsing so that temporary multipart spools are closed."""


class UploadBodyLimitMiddleware:
    """Bound upload admission and conversion bodies before request parsing."""

    def __init__(self, app: ASGIApp, limits: ResourceLimits, max_uploads: int = 5) -> None:
        if not isinstance(max_uploads, int) or isinstance(max_uploads, bool) or max_uploads <= 0:
            raise ValueError("max_uploads must be a positive integer")
        self.app = app
        self.limits = limits
        self.max_uploads = max_uploads
        self._active_uploads = 0
        self._uploads_lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "").rstrip("/")
        is_upload = path == "/convert/upload"
        is_json = path in ("/convert/path", "/convert/folder")
        if scope["type"] != "http" or not (is_upload or is_json):
            await self.app(scope, receive, send)
            return

        if is_upload:
            with self._uploads_lock:
                admitted = self._active_uploads < self.max_uploads
                if admitted:
                    self._active_uploads += 1
            if not admitted:
                await JSONResponse(
                    {"detail": f"Too many concurrent uploads; limit is {self.max_uploads}"},
                    status_code=429,
                    headers={"Retry-After": "1"},
                )(scope, receive, send)
                return
        try:
            await self._guard_body(scope, receive, send, is_upload=is_upload)
        finally:
            if is_upload:
                with self._uploads_lock:
                    self._active_uploads -= 1

    async def _guard_body(self, scope: Scope, receive: Receive, send: Send, *, is_upload: bool) -> None:
        maximum_bytes = self.limits.max_upload_body_bytes if is_upload else self.limits.max_json_body_bytes
        body_kind = "Upload" if is_upload else "JSON"
        lengths = [value.strip() for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or re.fullmatch(rb"[0-9]+", lengths[0]) is None:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                return
            # A very long decimal length is necessarily larger than the limit;
            # compare decimal strings instead of accepting arbitrarily large int input.
            declared = lengths[0].lstrip(b"0") or b"0"
            maximum = str(maximum_bytes).encode("ascii")
            if len(declared) > len(maximum) or (len(declared) == len(maximum) and declared > maximum):
                await self._reject(scope, receive, send, maximum_bytes, body_kind)
                return

        seen = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal seen, exceeded
            if exceeded:
                raise _RequestBodyTooLarge
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > maximum_bytes:
                    exceeded = True
                    raise _RequestBodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            # FastAPI translates generic parser exceptions into a 400. Suppress
            # that response when this guard interrupted the stream, then emit 413.
            if not exceeded:
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            pass
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            if response_started:
                # The upload route consumes its body before responding. For a
                # different streaming app, an already sent status cannot change.
                raise _RequestBodyTooLarge("Request exceeded its limit after response started")
            await self._reject(scope, receive, send, maximum_bytes, body_kind)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, maximum_bytes: int, body_kind: str) -> None:
        await JSONResponse(
            {"detail": f"{body_kind} request exceeds {maximum_bytes} bytes"},
            status_code=413,
        )(scope, receive, send)


def validate_local_file(path: Path, limits: ResourceLimits) -> int:
    """Check local inputs before scheduling a conversion and return their size."""
    try:
        metadata = path.stat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if metadata.st_size > limits.max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {limits.max_file_bytes} bytes: {path}",
        )
    return metadata.st_size


def copy_upload_bounded(source: BinaryIO, destination: Path, limits: ResourceLimits) -> int:
    """Copy at most the file limit, deleting a newly created partial file on failure."""
    created = False
    total = 0
    try:
        with destination.open("xb") as output:
            created = True
            while True:
                chunk = source.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_file_bytes:
                    raise HTTPException(status_code=413, detail=f"File exceeds {limits.max_file_bytes} bytes")
                output.write(chunk)
        return total
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def collect_folder_inputs(folder: Path, supported_extensions: set[str], limits: ResourceLimits) -> list[Path]:
    """Preflight a bounded flat folder batch without materializing every entry."""
    supported = {extension.lower() for extension in supported_extensions}
    inputs: list[Path] = []
    try:
        with os.scandir(folder) as entries:
            for scanned, entry in enumerate(entries, start=1):
                if scanned > limits.max_folder_entries:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Folder exceeds {limits.max_folder_entries} entries: {folder}",
                    )
                if Path(entry.name).suffix.lower() not in supported or not entry.is_file():
                    continue
                if len(inputs) >= limits.max_batch_files:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Folder exceeds {limits.max_batch_files} supported files: {folder}",
                    )
                path = Path(entry.path)
                validate_local_file(path, limits)
                inputs.append(path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}") from exc
    return sorted(inputs)
