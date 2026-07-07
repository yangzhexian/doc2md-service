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

    # 2. Install deps if needed
    if not DEPS_FLAG.is_file():
        log("Installing dependencies...")
        subprocess.run(
            [str(VENV_DIR / "Scripts" / "pip"), "install", "--upgrade", "pip", "--quiet"],
            check=True, capture_output=True,
        )
        subprocess.run(
            [str(VENV_DIR / "Scripts" / "pip"), "install", "-r",
             str(PROJECT_DIR / "requirements.txt")],
            check=True, capture_output=True,
        )
        DEPS_FLAG.write_text("", encoding="utf-8")
        log("Dependencies installed.")

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
    subprocess.Popen(
        [str(venv_pythonw), "-m", "uvicorn", "converter_service:app",
         "--app-dir", str(PROJECT_DIR / "src"),
         "--host", "127.0.0.1", "--port", port],
        stdout=subprocess.DEVNULL,
        stderr=open(str(PROJECT_DIR / "uvicorn.log"), "a", encoding="utf-8"),
    )
    log("uvicorn launched.")


if __name__ == "__main__":
    main()
