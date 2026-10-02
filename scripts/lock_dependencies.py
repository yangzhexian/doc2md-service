"""Regenerate or verify the portable dependency locks using a pinned resolver.

Install uv==0.9.30 in a separate tooling environment, then run this script with
that environment's Python. --check validates committed file hashes offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.9.30"
FILES = ("requirements.in", "requirements.txt", "requirements-test.in", "requirements-test.txt")
MANIFEST = PROJECT / "requirements.lock.json"


def file_hashes() -> dict[str, str]:
    # Git may check text out with CRLF on Windows; hashes describe the same
    # logical lock content on both platforms.
    return {name: hashlib.sha256((PROJECT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in FILES}


def check() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("uv") != UV_VERSION or manifest.get("files") != file_hashes():
        raise SystemExit("Dependency locks are stale; run python scripts/lock_dependencies.py.")
    print("Dependency lock inputs and outputs match the committed manifest.")


def regenerate() -> None:
    version = subprocess.check_output([sys.executable, "-m", "uv", "--version"], text=True).strip()
    if version.split()[:2] != ["uv", UV_VERSION]:
        raise SystemExit(f"Expected uv {UV_VERSION}; install it in the tooling environment (found {version}).")
    for source, output in (("requirements.in", "requirements.txt"),
                           ("requirements-test.in", "requirements-test.txt")):
        subprocess.run([
            sys.executable, "-m", "uv", "pip", "compile", source,
            "--universal", "--python-version", "3.10", "--generate-hashes",
            "--default-index", "https://pypi.org/simple", "--no-config", "--quiet",
            "--custom-compile-command", "python scripts/lock_dependencies.py",
            "--output-file", output,
        ], cwd=PROJECT, check=True)
    manifest = {
        "uv": UV_VERSION,
        "python": ">=3.10,<3.15",
        "tested_python": "3.12",
        "index": "https://pypi.org/simple",
        "files": file_hashes(),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    check()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify file hashes without network access")
    if parser.parse_args().check:
        check()
    else:
        regenerate()
