"""One isolated conversion worker; run only by resource_control."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict
from functools import wraps
from importlib.metadata import distribution
from pathlib import Path


def configure_native_runtime(threads: int) -> None:
    """Clamp ONNX sessions inside this worker without altering provider choices."""
    try:
        import onnxruntime as ort
    except ImportError:
        return
    current = ort.InferenceSession.__init__
    original = getattr(current, "_docs2md_original_init", current)

    @wraps(original)
    def limited_session(self, path_or_bytes, sess_options=None, *args, **kwargs):
        options = sess_options if sess_options is not None else ort.SessionOptions()
        if options.intra_op_num_threads <= 0 or options.intra_op_num_threads > threads:
            options.intra_op_num_threads = threads
        if options.inter_op_num_threads <= 0 or options.inter_op_num_threads > 1:
            options.inter_op_num_threads = 1
        return original(self, path_or_bytes, options, *args, **kwargs)

    limited_session._docs2md_original_init = original
    ort.InferenceSession.__init__ = limited_session


def configure_llama_runtime(threads: int) -> None:
    """Set llama.cpp's independent C++ thread pool at its SDK boundary."""
    try:
        from mineru_llama_cpp import Engine
    except (ImportError, OSError):
        return
    current = Engine.__init__
    original = getattr(current, "_docs2md_original_init", current)

    @wraps(original)
    def limited_engine(self, *args, **kwargs):
        requested = kwargs.get("n_threads", -1)
        if requested <= 0 or requested > threads:
            kwargs["n_threads"] = threads
        return original(self, *args, **kwargs)

    limited_engine._docs2md_original_init = original
    Engine.__init__ = limited_engine


def configure_torch_runtime(threads: int) -> None:
    """Apply PyTorch thread settings in the actual inference interpreter."""
    try:
        import torch
    except (ImportError, OSError):
        return
    current = torch.get_num_threads()
    torch.set_num_threads(min(current, threads) if current > 0 else threads)
    # PyTorch allows this setting only before inter-op work starts. Bootstrap
    # runs once, before any model or parser is imported, in each fresh process.
    torch.set_num_interop_threads(1)


def prepare_runtime() -> None:
    # Set tempfile's cache before importing engines, whose descendants inherit
    # this same job-owned temporary directory through TMP/TEMP/TMPDIR.
    temporary = os.environ.get("TMPDIR")
    if temporary:
        tempfile.tempdir = temporary
    threads = int(os.environ.get("DOCS2MD_WORKER_THREADS", "2"))
    configure_native_runtime(threads)
    configure_llama_runtime(threads)
    configure_torch_runtime(threads)


def run_mineru_cli(arguments: list[str]) -> object:
    """Run the installed CLI with the same caps as its supervising worker."""
    prepare_runtime()
    # Use the installed distribution's entry point, retaining its normal CLI
    # parsing and selected backend; no dependency source is rewritten.
    entry = next(point for point in distribution("mineru").entry_points if point.name == "mineru-kit")
    sys.argv = ["mineru-kit", *arguments]
    return entry.load()()


def main(request_path: Path, response_path: Path) -> None:
    prepare_runtime()
    from fastapi import HTTPException
    from converter_service import _run_conversion_uncontrolled
    from engines.base import ConvertOptions

    request = json.loads(request_path.read_text(encoding="utf-8"))
    options = dict(request["options"])
    if options.get("output_dir") is not None:
        options["output_dir"] = Path(options["output_dir"])
    try:
        status = _run_conversion_uncontrolled(
            Path(request["file_path"]), engine_name=request.get("engine_name"),
            output_dir=Path(request["output_dir"]) if request.get("output_dir") is not None else None,
            options=ConvertOptions(**options),
        )
        response = {"kind": "result", "data": asdict(status)}
    except HTTPException as exc:
        detail = exc.detail[:8192] if isinstance(exc.detail, str) else exc.detail
        response = {"kind": "http_error", "status_code": exc.status_code, "detail": detail, "headers": exc.headers}
    except Exception as exc:
        response = {"kind": "http_error", "status_code": 500, "detail": f"Conversion worker failed: {exc}"[:8192]}
    # The parent never reads a partially written response.
    staged = response_path.with_suffix(".tmp")
    staged.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    os.replace(staged, response_path)


if __name__ == "__main__":
    if sys.argv[1] == "--mineru-cli":
        sys.exit(run_mineru_cli(sys.argv[2:]))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
