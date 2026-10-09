"""Resource bounds reject work before conversion and clean partial uploads."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from resource_limits import (  # noqa: E402
    MIB,
    UPLOAD_CHUNK_BYTES,
    ResourceLimits,
    UploadBodyLimitMiddleware,
    collect_folder_inputs,
    copy_upload_bounded,
    validate_local_file,
)


class ResourceConfigurationTests(unittest.TestCase):
    def test_defaults_and_explicit_overrides(self) -> None:
        defaults = ResourceLimits.from_env({})
        self.assertEqual(defaults.max_file_bytes, 100 * MIB)
        self.assertEqual(defaults.max_upload_body_bytes, 101 * MIB)
        self.assertEqual(defaults.max_batch_files, 100)
        self.assertEqual(defaults.max_folder_entries, 10_000)
        self.assertEqual(defaults.max_json_body_bytes, 65_536)
        configured = ResourceLimits.from_env({
            "DOCS2MD_MAX_FILE_BYTES": " 12 ",
            "DOCS2MD_MAX_BATCH_FILES": "3",
            "DOCS2MD_MAX_FOLDER_ENTRIES": "20",
        })
        self.assertEqual(configured, ResourceLimits(12, 3, 20))

    def test_invalid_limits_fail_fast_instead_of_disabling_bounds(self) -> None:
        names = ("DOCS2MD_MAX_FILE_BYTES", "DOCS2MD_MAX_BATCH_FILES", "DOCS2MD_MAX_FOLDER_ENTRIES")
        for name in names:
            for value in ("", "0", "-1", "+1", "1.5", "all", "١", "1_000"):
                with self.subTest(name=name, value=value):
                    with self.assertRaisesRegex(ValueError, name):
                        ResourceLimits.from_env({name: value})
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ResourceLimits(max_file_bytes=value)
                with self.assertRaises(ValueError):
                    UploadBodyLimitMiddleware(None, ResourceLimits(), max_uploads=value)


class FileLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docs2md_limits_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.limits = ResourceLimits(max_file_bytes=8, max_batch_files=2, max_folder_entries=4)

    def test_local_file_boundary_and_missing_inputs(self) -> None:
        source = self.root / "note.txt"
        source.write_bytes(b"12345678")
        self.assertEqual(validate_local_file(source, self.limits), 8)
        source.write_bytes(b"123456789")
        with self.assertRaises(HTTPException) as oversize:
            validate_local_file(source, self.limits)
        self.assertEqual(oversize.exception.status_code, 413)
        self.assertEqual(source.read_bytes(), b"123456789")
        for path in (self.root / "missing.txt", self.root):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException) as missing:
                    validate_local_file(path, self.limits)
                self.assertEqual(missing.exception.status_code, 404)

    def test_upload_copy_accepts_boundary_and_empty_files(self) -> None:
        for content in (b"", b"12345678"):
            with self.subTest(content=content):
                destination = self.root / f"{len(content)}.txt"
                self.assertEqual(copy_upload_bounded(io.BytesIO(content), destination, self.limits), len(content))
                self.assertEqual(destination.read_bytes(), content)

    def test_upload_copy_reads_bounded_chunks_and_removes_oversize_partial(self) -> None:
        requested: list[int] = []

        class Chunks(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                requested.append(size)
                return super().read(min(size, 4))

        destination = self.root / "partial.txt"
        with self.assertRaises(HTTPException) as exceeded:
            copy_upload_bounded(Chunks(b"123456789"), destination, self.limits)
        self.assertEqual(exceeded.exception.status_code, 413)
        self.assertTrue(requested)
        self.assertTrue(all(size == UPLOAD_CHUNK_BYTES for size in requested))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_upload_copy_preserves_existing_file_and_cleans_read_failure(self) -> None:
        destination = self.root / "note.txt"
        destination.write_bytes(b"previous")
        with self.assertRaises(FileExistsError):
            copy_upload_bounded(io.BytesIO(b"replacement"), destination, self.limits)
        self.assertEqual(destination.read_bytes(), b"previous")
        destination.unlink()

        class BrokenSource:
            calls = 0

            def read(self, size: int) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return b"part"
                raise OSError("source read failed")

        with self.assertRaisesRegex(OSError, "source read failed"):
            copy_upload_bounded(BrokenSource(), destination, self.limits)
        self.assertFalse(destination.exists())

    def test_folder_preflight_sorts_supported_files_and_ignores_subfolders(self) -> None:
        (self.root / "b.TXT").write_bytes(b"b")
        (self.root / "a.txt").write_bytes(b"a")
        (self.root / "ignored.bin").write_bytes(b"not supported")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "c.txt").write_bytes(b"nested")
        actual = collect_folder_inputs(self.root, {".txt"}, self.limits)
        self.assertEqual(actual, [self.root / "a.txt", self.root / "b.TXT"])

    def test_folder_preflight_rejects_too_many_supported_files(self) -> None:
        for index in range(3):
            (self.root / f"{index}.txt").write_bytes(b"text")
        with self.assertRaises(HTTPException) as exceeded:
            collect_folder_inputs(self.root, {".txt"}, self.limits)
        self.assertEqual(exceeded.exception.status_code, 413)
        self.assertIn("2 supported files", exceeded.exception.detail)
        self.assertEqual(len(list(self.root.iterdir())), 3)

    def test_folder_preflight_rejects_large_file_before_returning_a_batch(self) -> None:
        (self.root / "a.txt").write_bytes(b"small")
        (self.root / "b.txt").write_bytes(b"too large")
        with self.assertRaises(HTTPException) as exceeded:
            collect_folder_inputs(self.root, {".txt"}, self.limits)
        self.assertEqual(exceeded.exception.status_code, 413)
        self.assertIn("8 bytes", exceeded.exception.detail)
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_folder_preflight_bounds_unsupported_entry_scan(self) -> None:
        for index in range(5):
            (self.root / f"{index}.bin").write_bytes(b"")
        with self.assertRaises(HTTPException) as exceeded:
            collect_folder_inputs(self.root, {".txt"}, self.limits)
        self.assertEqual(exceeded.exception.status_code, 413)
        self.assertIn("4 entries", exceeded.exception.detail)

    def test_folder_entry_iterator_stops_at_first_excess_entry(self) -> None:
        visited: list[int] = []

        class Entry:
            name = "ignored.bin"

        def entries():
            for index in range(100):
                visited.append(index)
                yield Entry()

        class Scan:
            def __enter__(self):
                return entries()

            def __exit__(self, *exc):
                return False

        with patch("resource_limits.os.scandir", return_value=Scan()):
            with self.assertRaises(HTTPException):
                collect_folder_inputs(self.root, {".txt"}, self.limits)
        self.assertEqual(visited, list(range(5)))

    def test_missing_or_nonfolder_preflight_reports_not_found(self) -> None:
        source = self.root / "note.txt"
        source.write_bytes(b"note")
        for path in (self.root / "missing", source):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException) as missing:
                    collect_folder_inputs(path, {".txt"}, self.limits)
                self.assertEqual(missing.exception.status_code, 404)


class UploadBodyMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, app, chunks, *, headers=(), path="/convert/upload", scope_type="http", receive_hook=None):
        messages = []
        reads = []
        source = iter(chunks)

        async def receive():
            if receive_hook is not None:
                await receive_hook()
            try:
                body = next(source)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}
            reads.append(len(body))
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message):
            messages.append(message)

        scope = {
            "type": scope_type, "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path, "root_path": "",
            "query_string": b"", "headers": list(headers), "server": ("localhost", 80),
            "client": ("localhost", 50000),
        }
        await app(scope, receive, send)
        return messages, reads

    def consuming_app(self, completed):
        async def app(scope, receive, send):
            total = 0
            while True:
                message = await receive()
                total += len(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            completed.append(total)
            await JSONResponse({"size": total})(scope, receive, send)
        return app

    def status(self, messages):
        starts = [message for message in messages if message["type"] == "http.response.start"]
        self.assertEqual(len(starts), 1)
        return starts[0]["status"]

    async def test_declared_oversize_rejects_before_app_or_receive(self) -> None:
        completed = []
        limits = ResourceLimits(max_file_bytes=8)
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), limits)
        for length in (str(limits.max_upload_body_bytes + 1).encode(), b"9" * 5000):
            with self.subTest(length=length[:20]):
                messages, reads = await self.invoke(middleware, [b"ignored"], headers=[(b"content-length", length)])
                self.assertEqual(self.status(messages), 413)
                self.assertEqual(reads, [])
                self.assertEqual(completed, [])

    async def test_actual_stream_size_rejects_missing_and_lying_lengths(self) -> None:
        limits = ResourceLimits(max_file_bytes=8)
        for headers in ([], [(b"content-length", b"1")]):
            completed = []
            middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), limits)
            messages, reads = await self.invoke(middleware, [b"x" * MIB, b"123456789", b"unread"], headers=headers)
            self.assertEqual(self.status(messages), 413)
            self.assertEqual(reads, [MIB, 9])
            self.assertEqual(completed, [])

    async def test_chunked_request_accepts_exact_boundary_without_buffering(self) -> None:
        completed = []
        limits = ResourceLimits(max_file_bytes=8)
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), limits)
        messages, reads = await self.invoke(middleware, [b"x" * MIB, b"12345678"])
        self.assertEqual(self.status(messages), 200)
        self.assertEqual(completed, [limits.max_upload_body_bytes])
        self.assertEqual(reads, [MIB, 8])

    async def test_invalid_and_duplicate_lengths_reject_without_reading(self) -> None:
        completed = []
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), ResourceLimits())
        for headers in ([(b"content-length", b"-1")], [(b"content-length", b"1.5")], [(b"content-length", b"")], [(b"content-length", b"1"), (b"content-length", b"1")]):
            with self.subTest(headers=headers):
                messages, reads = await self.invoke(middleware, [b"ignored"], headers=headers)
                self.assertEqual(self.status(messages), 400)
                self.assertEqual(reads, [])
        self.assertEqual(completed, [])

    async def test_other_route_body_is_not_limited_by_upload_guard(self) -> None:
        completed = []
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), ResourceLimits(max_file_bytes=8))
        body = b"x" * (MIB + 9)
        messages, _ = await self.invoke(middleware, [body], path="/other")
        self.assertEqual(self.status(messages), 200)
        self.assertEqual(completed, [len(body)])

    async def test_upload_admission_rejects_excess_before_body_receive_and_keeps_health_available(self) -> None:
        completed = []
        entered = asyncio.Event()
        release = asyncio.Event()
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), ResourceLimits(), max_uploads=1)

        async def hold_receive():
            entered.set()
            await release.wait()

        first = asyncio.create_task(self.invoke(middleware, [b"first"], receive_hook=hold_receive))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            messages, reads = await self.invoke(middleware, [b"do not read this"])
            self.assertEqual(self.status(messages), 429)
            self.assertEqual(reads, [])
            self.assertEqual(completed, [])
            start = next(message for message in messages if message["type"] == "http.response.start")
            self.assertIn((b"retry-after", b"1"), start["headers"])
            health, _ = await self.invoke(middleware, [], path="/health")
            self.assertEqual(self.status(health), 200)
            self.assertEqual(completed, [0])
        finally:
            release.set()
            first_messages, _ = await asyncio.wait_for(first, timeout=2)
        self.assertEqual(self.status(first_messages), 200)
        later, _ = await self.invoke(middleware, [b"later"])
        self.assertEqual(self.status(later), 200)
        self.assertEqual(completed, [0, 5, 5])

    async def test_upload_admission_is_released_after_app_error(self) -> None:
        calls = []

        async def app(scope, receive, send):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("failure before response")
            await JSONResponse({"ok": True})(scope, receive, send)

        middleware = UploadBodyLimitMiddleware(app, ResourceLimits(), max_uploads=1)
        with self.assertRaisesRegex(RuntimeError, "failure before response"):
            await self.invoke(middleware, [])
        messages, _ = await self.invoke(middleware, [])
        self.assertEqual(self.status(messages), 200)
        self.assertEqual(len(calls), 2)

    async def test_upload_admission_is_released_after_cancelled_stream(self) -> None:
        completed = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_receive():
            entered.set()
            await release.wait()

        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), ResourceLimits(), max_uploads=1)
        first = asyncio.create_task(self.invoke(middleware, [], receive_hook=hold_receive))
        await asyncio.wait_for(entered.wait(), timeout=2)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        later, _ = await self.invoke(middleware, [])
        self.assertEqual(self.status(later), 200)
        self.assertEqual(completed, [0])

    async def test_upload_admission_is_released_after_size_rejection(self) -> None:
        completed = []
        limits = ResourceLimits(max_file_bytes=8)
        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), limits, max_uploads=1)
        rejected, _ = await self.invoke(middleware, [b"x" * (MIB + 9)])
        self.assertEqual(self.status(rejected), 413)
        accepted, _ = await self.invoke(middleware, [b"small"])
        self.assertEqual(self.status(accepted), 200)
        self.assertEqual(completed, [5])

    async def test_json_routes_bound_declared_and_actual_body_before_endpoint(self) -> None:
        limits = ResourceLimits()
        for path in ("/convert/path", "/convert/folder"):
            for headers in ([], [(b"content-length", b"1")], [(b"content-length", b"65537")]):
                with self.subTest(path=path, headers=headers):
                    completed = []
                    middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), limits)
                    messages, reads = await self.invoke(middleware, [b"x" * 65_536, b"!"], headers=headers, path=path)
                    self.assertEqual(self.status(messages), 413)
                    self.assertEqual(completed, [])
                    self.assertEqual(reads, [] if headers == [(b"content-length", b"65537")] else [65_536, 1])
                    detail = json.loads(b"".join(message.get("body", b"") for message in messages))["detail"]
                    self.assertIn("JSON request exceeds 65536 bytes", detail)

    async def test_json_body_accepts_exact_boundary_and_does_not_take_upload_admission(self) -> None:
        completed = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_receive():
            entered.set()
            await release.wait()

        middleware = UploadBodyLimitMiddleware(self.consuming_app(completed), ResourceLimits(), max_uploads=1)
        upload = asyncio.create_task(self.invoke(middleware, [], receive_hook=hold_receive))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            messages, _ = await self.invoke(middleware, [b"x" * 65_536], path="/convert/path")
            self.assertEqual(self.status(messages), 200)
            self.assertEqual(completed, [65_536])
        finally:
            release.set()
            await asyncio.wait_for(upload, timeout=2)

    async def test_actual_fastapi_json_route_rejects_before_json_decoding(self) -> None:
        app = FastAPI()
        converted = []

        @app.post("/convert/path")
        @app.post("/convert/folder")
        async def convert(payload: dict):
            converted.append(payload)
            return {"ok": True}

        middleware = UploadBodyLimitMiddleware(app, ResourceLimits())
        for path in ("/convert/path", "/convert/folder"):
            with self.subTest(path=path):
                with patch("starlette.requests.json.loads", wraps=json.loads) as decode:
                    messages, _ = await self.invoke(
                        middleware,
                        [b'{"file_path":"' + b"x" * 65_536 + b'"}'],
                        headers=[(b"content-type", b"application/json")],
                        path=path,
                    )
                    decode.assert_not_called()
                self.assertEqual(self.status(messages), 413)
                self.assertEqual(converted, [])

    async def test_unrelated_application_exception_propagates(self) -> None:
        async def broken(scope, receive, send):
            raise RuntimeError("unrelated failure")

        middleware = UploadBodyLimitMiddleware(broken, ResourceLimits())
        with self.assertRaisesRegex(RuntimeError, "unrelated failure"):
            await self.invoke(middleware, [])

    async def test_actual_multipart_parser_closes_spooled_files_on_oversize(self) -> None:
        app = FastAPI()
        converted = []

        @app.post("/convert/upload")
        async def upload(file: UploadFile = File(...)):
            converted.append(file.filename)
            return {"ok": True}

        limits = ResourceLimits(max_file_bytes=MIB + 8)
        middleware = UploadBodyLimitMiddleware(app, limits)
        prefix = b'--bound\r\nContent-Disposition: form-data; name="file"; filename="note.txt"\r\nContent-Type: text/plain\r\n\r\n'
        spools = []
        original_spool = tempfile.SpooledTemporaryFile

        def capture_spool(*args, **kwargs):
            spool = original_spool(*args, **kwargs)
            spools.append(spool)
            return spool

        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=capture_spool):
            messages, reads = await self.invoke(
                middleware,
                [prefix + b"x" * MIB, b"x" * 100, b"x" * MIB, b"unread"],
                headers=[(b"content-type", b"multipart/form-data; boundary=bound")],
            )
        self.assertEqual(self.status(messages), 413)
        detail = json.loads(b"".join(message.get("body", b"") for message in messages))["detail"]
        self.assertIn("Upload request exceeds", detail)
        self.assertEqual(len(reads), 3)
        self.assertEqual(converted, [])
        self.assertEqual(len(spools), 1)
        self.assertTrue(spools[0]._rolled)
        self.assertTrue(spools[0].closed)


if __name__ == "__main__":
    unittest.main()
