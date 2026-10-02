"""Actual document regressions for the locked CPU installation.

The default suite does real MarkItDown and MinerU CLI conversions without models.
DOCS2MD_RUN_OCR=1 adds the PNG OCR case, requiring an existing local model package
at DOCS2MD_OCR_MODEL_DIR. No tests download models. Write a machine-readable report
with DOCS2MD_REAL_REGRESSION_REPORT=/path/to/real-documents.json.

The converter worker is a fresh subprocess: other tests can import MinerU's config
singleton safely without leaking production settings into these conversions.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import traceback
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "real_documents"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
RUN_OCR = os.environ.get("DOCS2MD_RUN_OCR") == "1"
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))[^)]*\)")
HTML_IMAGE_RE = re.compile(r'<img\b[^>]*\bsrc=[\'"]([^\'"]+)[\'"]', re.IGNORECASE)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_corpus() -> None:
    for name, expected in MANIFEST["files"].items():
        path = FIXTURES / name
        if file_digest(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise AssertionError(f"Fixture differs from its provenance manifest: {name}")


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def isolated_environment(root: Path) -> dict[str, str]:
    # Strip all inherited MinerU settings, including legacy API keys/remote URLs.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("MINERU_", "DOCS2MD_MINERU_"))
    }
    home = root / "mineru-home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text(
        "model:\n  source: local\n  small_backend: onnx\n"
        "doclib:\n  uds:\n    enabled: false\n  tcp:\n    host: 127.0.0.1\n    port: 0\n"
        "  managed_parse_server:\n    host: 127.0.0.1\n    port: 0\n",
        encoding="utf-8",
    )
    environment.update({
        "MINERU_HOME": str(home),
        "MINERU_CONFIG": str(config),
        "MINERU_MODEL_BASE_DIR": str(root / "empty-models"),
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "onnx",
        "HF_HOME": str(root / "huggingface"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "MODELSCOPE_CACHE": str(root / "modelscope"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DOCS2MD_MINERU_TIMEOUT": "120",
        "DOCS2MD_MINERU_OUTPUT_READY_GRACE": "2",
        "DOCS2MD_RUN_OCR": "1" if RUN_OCR else "0",
    })
    (root / "empty-models").mkdir()
    return environment


def assert_semantics(markdown: str, output: Path, case: dict, engine: str) -> list[str]:
    assertions = []

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)
        assertions.append(label)

    require(bool(markdown.strip()), "nonempty Markdown")
    normalized = " ".join(markdown.split())
    for token in case.get("contains", []):
        require(token in normalized, f"contains {token!r}")
    for token in case.get("excludes", []):
        require(token not in normalized, f"excludes {token!r}")
    for link in case.get("links", []):
        require(link in markdown, f"preserves link {link}")
    cells = case.get("table_cells", []) + case.get(f"{engine}_table_cells", [])
    if cells:
        # Allow either pipe Markdown or HTML tables; don't snapshot formatting.
        table_lines = [line for line in markdown.splitlines() if "|" in line or "<td" in line or "<th" in line]
        table_text = " ".join(table_lines)
        for cell in cells:
            require(cell in table_text, f"table cell {cell!r}")

    destinations = [first or second for first, second in IMAGE_RE.findall(markdown)]
    destinations += HTML_IMAGE_RE.findall(markdown)
    if engine == "mineru":
        local_targets = []
        for destination in destinations:
            parsed = urlsplit(destination)
            if parsed.scheme in {"data", "https", "http"}:
                continue
            require(not parsed.scheme and not parsed.netloc, f"local image destination {destination}")
            target = (output.parent / unquote(parsed.path)).resolve()
            require(target.is_relative_to(output.parent.resolve()), f"image stays within output: {destination}")
            require(target.is_file() and target.stat().st_size > 0, f"exported image exists: {destination}")
            local_targets.append(target)
        minimum = case.get("mineru_min_images", 0)
        require(len(local_targets) >= minimum, f"at least {minimum} exported images")
    if engine == "markitdown" and "markitdown_image_target" in case:
        require(case["markitdown_image_target"] in destinations, "preserves original HTML image destination")
    return assertions


def worker(report_path: Path, workspace: Path) -> None:
    sys.path.insert(0, str(PROJECT / "src"))
    from engines.base import ConvertOptions
    from engines.markitdown import MarkItDownEngine
    from engines.mineru import MinerUEngine, _find_mineru_kit_bin

    report = {
        "schema_version": 1,
        "fixture_manifest_sha256": file_digest(FIXTURES / "manifest.json"),
        "python": platform.python_version(),
        "platform": sys.platform,
        "versions": {name: importlib.metadata.version(name) for name in ("mineru", "markitdown")},
        "mineru_cli": _find_mineru_kit_bin(),
        "model_source": "local",
        "cases": [],
    }
    write_report(report_path, report)
    verify_corpus()
    report["fixture_integrity"] = "verified"
    binary_name = "mineru-kit.exe" if os.name == "nt" else "mineru-kit"
    expected_cli = Path(sysconfig.get_path("scripts")) / binary_name
    if not expected_cli.is_file():
        raise AssertionError(f"The tested interpreter environment has no installed MinerU CLI: {expected_cli}")
    if Path(report["mineru_cli"] or "").resolve() != expected_cli.resolve():
        raise AssertionError(f"The MinerU CLI must come from the tested interpreter environment: expected {expected_cli}, got {report['mineru_cli']}")
    started = time.perf_counter()
    input_dir = workspace / "inputs"
    input_dir.mkdir()
    for name in MANIFEST["files"]:
        target = input_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name, target)
    source_hashes = {name: file_digest(input_dir / name) for name in MANIFEST["files"]}
    converters = {"markitdown": MarkItDownEngine(), "mineru": MinerUEngine()}
    for case in MANIFEST["cases"]:
        for engine_name in case["engines"]:
            record = {"id": f"{engine_name}_{case['id']}", "engine": engine_name, "fixture": case["file"], "tier": "flash" if engine_name == "mineru" else None, "pages": case.get("pages", "all"), "assertions": []}
            if case.get("requires_models") and not RUN_OCR:
                record.update(status="skipped", elapsed_seconds=0, reason="Optional OCR: set DOCS2MD_RUN_OCR=1 and DOCS2MD_OCR_MODEL_DIR to installed local models.")
                report["cases"].append(record)
                write_report(report_path, report)
                continue
            case_started = time.perf_counter()
            default_models = os.environ["MINERU_MODEL_BASE_DIR"]
            try:
                if case.get("requires_models"):
                    model_dir = os.environ.get("DOCS2MD_OCR_MODEL_DIR", "")
                    if not model_dir or not Path(model_dir).is_dir():
                        raise AssertionError("OCR was requested but DOCS2MD_OCR_MODEL_DIR is not an existing model directory")
                    os.environ["MINERU_MODEL_BASE_DIR"] = str(Path(model_dir).resolve())
                    # An existing ONNX package is portable across CPU runners.
                    # For local Torch-only installs opt in explicitly; no download.
                    os.environ["MINERU_MODEL_SMALL_BACKEND"] = os.environ.get("DOCS2MD_OCR_BACKEND", "onnx")
                options = ConvertOptions(output_dir=workspace / "outputs" / record["id"], mineru_tier="flash", mineru_pages=case.get("pages", "all"))
                engine = converters[engine_name]
                source = input_dir / case["file"]
                engine.validate_options(options, source)
                result = engine.convert(source, options)
                if result.error:
                    raise AssertionError(result.error)
                if result.engine != engine_name or result.fallback:
                    raise AssertionError(f"Expected actual {engine_name} conversion without fallback: {result}")
                output = Path(result.output_path)
                markdown = output.read_text(encoding="utf-8")
                record["assertions"] = assert_semantics(markdown, output, case, engine_name)
                for name, expected in source_hashes.items():
                    if file_digest(input_dir / name) != expected:
                        raise AssertionError(f"Converter modified input or resource: {name}")
                record["assertions"].append("all inputs and linked source resources unchanged")
                if not case.get("requires_models") and any((workspace / "empty-models").iterdir()):
                    raise AssertionError("Model-free regression populated the empty model directory")
                record["assertions"].append("no model download")
                record.update(status="passed", markdown_bytes=output.stat().st_size, error=None)
            except Exception as exc:
                record.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
            finally:
                os.environ["MINERU_MODEL_BASE_DIR"] = default_models
                os.environ["MINERU_MODEL_SMALL_BACKEND"] = "onnx"
            record["elapsed_seconds"] = round(time.perf_counter() - case_started, 3)
            report["cases"].append(record)
            report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            write_report(report_path, report)
    report["summary"] = {status: sum(record["status"] == status for record in report["cases"]) for status in ("passed", "failed", "skipped")}
    write_report(report_path, report)


class RealDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="docs2md-real-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.report_path = Path(os.environ.get("DOCS2MD_REAL_REGRESSION_REPORT", str(root / "report.json"))).resolve()
        cls.results = {}
        # Clear a previous run before launching so worker failures can't reuse
        # stale successful case records in the explicitly requested artifact.
        write_report(cls.report_path, {"schema_version": 1, "cases": []})
        environment = isolated_environment(root)
        log_path = root / "worker.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(cls.report_path), str(root)],
                cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
                code = -1
        if code != 0:
            error = f"Actual converter worker exited with {code}:\n{log_path.read_text(encoding='utf-8', errors='replace')[-12000:]}"
            if cls.report_path.is_file():
                report = json.loads(cls.report_path.read_text(encoding="utf-8"))
            else:
                report = {"schema_version": 1, "cases": []}
            report["worker_error"] = error
            write_report(cls.report_path, report)
            cls.worker_error = error
        else:
            cls.worker_error = None
        if cls.report_path.is_file():
            report = json.loads(cls.report_path.read_text(encoding="utf-8"))
            cls.results = {record["id"]: record for record in report["cases"]}

    def test_fixture_hashes_match_provenance(self) -> None:
        verify_corpus()


def add_case_test(case: dict, engine: str) -> None:
    case_id = f"{engine}_{case['id']}"

    def test(self: RealDocumentTests) -> None:
        if case.get("requires_models") and not RUN_OCR:
            self.skipTest("Optional OCR requires DOCS2MD_RUN_OCR=1 and preinstalled local models")
        self.assertIsNone(self.worker_error, self.worker_error)
        self.assertIn(case_id, self.results, f"Actual conversion case missing from {self.report_path}")
        result = self.results[case_id]
        self.assertEqual(result["status"], "passed", result.get("traceback", result.get("error")))

    test.__doc__ = f"Real {engine} conversion: {case['file']}; pages={case.get('pages', 'all')}."
    setattr(RealDocumentTests, f"test_{case_id}", test)


for _case in MANIFEST["cases"]:
    for _engine in _case["engines"]:
        add_case_test(_case, _engine)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        unittest.main()
