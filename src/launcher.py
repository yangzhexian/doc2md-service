"""
docs2md background launcher.

Used by start.bat / start.vbs to launch the service without any CMD window.
Runs setup (venv, pip) then starts uvicorn -- all via pythonw.exe.
"""
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_DIR / "venv"
DEPS_FLAG = VENV_DIR / ".deps_installed"
LOG_FILE = PROJECT_DIR / "launcher.log"


def log(msg: str) -> None:
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{msg}\n")


def _requirements_hash() -> str:
    # Keep Windows and Unix installation markers identical.
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("docs2md_requirements_hash", PROJECT_DIR / "scripts/requirements_hash.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.requirements_hash(PROJECT_DIR / "requirements.txt")


def _tail(value: str, limit: int = 4000) -> str:
    """Keep launcher.log useful when a package manager emits a large error."""
    value = value.strip()
    return value if len(value) <= limit else "..." + value[-limit:]


def _runtime_dependencies_available(venv_python: Path) -> bool:
    """Check whether the already-installed runtime can start the service."""
    check = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import fastapi, uvicorn, markitdown, mineru, pydantic, multipart, loguru",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        log("Existing runtime dependency check failed:")
        log(_tail(check.stderr or check.stdout))
        return False
    return True


def _install_dependencies(venv_python: Path, req_hash: str) -> None:
    """Install dependencies and log package-manager failures in full enough detail."""
    commands = [
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        [str(venv_python), "-m", "pip", "install", "--require-hashes", "-r", str(PROJECT_DIR / "requirements.txt")],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            log(f"Command failed ({result.returncode}): {' '.join(command)}")
            log(_tail(result.stdout))
            log(_tail(result.stderr))
            raise RuntimeError(f"dependency command failed with exit code {result.returncode}")
    DEPS_FLAG.write_text(req_hash, encoding="utf-8")
    log("Dependencies installed.")


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "8000"
    log(f"=== launcher.py port={port} ===")

    # 1. Ensure venv exists
    venv_python = VENV_DIR / "Scripts" / "python.exe"
    venv_pythonw = VENV_DIR / "Scripts" / "pythonw.exe"
    if not venv_python.is_file():
        log("Creating venv...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            check=True, capture_output=True,
        )
        log("Venv created.")

    # 2. Install pinned deps when locks or their editable inputs change.
    # A failed install leaves the hash unchanged so the next launch retries.
    req_hash = _requirements_hash()
    installed_hash = DEPS_FLAG.read_text(encoding="utf-8").strip() if DEPS_FLAG.is_file() else ""
    if installed_hash != req_hash:
        log("Installing / upgrading dependencies...")
        try:
            _install_dependencies(venv_python, req_hash)
        except Exception as exc:
            log(f"Dependency installation failed: {exc}")
            if _runtime_dependencies_available(venv_python):
                log("Using the existing runtime and continuing to start the service.")
            else:
                log("Existing runtime is incomplete; service will not start.")
                return

    # 3. Skip if already running
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
        log(f"Service already running on {port}. Exiting.")
        return
    except urllib.error.URLError:
        pass

    # 4. Launch uvicorn via pythonw.exe (no console window)
    log(f"Starting uvicorn on {port}...")
    os.chdir(str(PROJECT_DIR))
    with open(str(PROJECT_DIR / "uvicorn.log"), "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [str(venv_pythonw), "-m", "uvicorn", "converter_service:app",
             "--app-dir", str(PROJECT_DIR / "src"),
             "--host", "127.0.0.1", "--port", port],
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
    log("uvicorn launched.")


if __name__ == "__main__":
    main()
