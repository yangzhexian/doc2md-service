#!/usr/bin/env python3
"""Download or update local MinerU 4.x models.

MinerU >= 4.0 ships quality-tier model packages under ``mineru_models/``:

- ``MinerU-4_models_torch`` / ``MinerU-4_models_onnx`` — small models
  (layout / formula / OCR / table), required by ``basic`` and above
- ``MinerU2.5-Pro-2605-1.2B`` (or ``-GGUF``) — VLM, required by
  ``standard`` / ``advanced``

Usage:
    python scripts/update.py                     # standard tier, auto source
    python scripts/update.py modelscope          # force ModelScope
    python scripts/update.py huggingface --tier basic
    python scripts/update.py --tier standard
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from model_manager import (  # noqa: E402
    ensure_models_dir,
    get_model_root,
    models_look_complete,
    small_models_present,
    vlm_models_present,
    write_mineru_config,
)

# ``mineru-kit models download/verify --tier`` accepts basic|standard only.
# ``advanced`` uses the same package as ``standard``.
_DOWNLOAD_TIER = {"basic": "basic", "standard": "standard", "advanced": "standard"}


def _resolve_source(arg: str | None) -> str:
    if arg in ("huggingface", "modelscope"):
        return arg
    return "auto"


def _find_mineru_kit() -> Path:
    scripts_dir = Path(sys.executable).parent
    name = "mineru-kit.exe" if sys.platform == "win32" else "mineru-kit"
    candidate = scripts_dir / name
    if candidate.is_file():
        return candidate
    project_venv = (
        _PROJECT_ROOT / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / name
    )
    if project_venv.is_file():
        return project_venv
    path_binary = shutil.which("mineru-kit")
    if path_binary is None:
        print(
            "ERROR: mineru-kit not found. "
            "Install it with: pip install 'mineru>=4.0,<5'",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(path_binary)


def _run(cmd: list[str], env: dict[str, str]) -> None:
    print(f"==> Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env)


def _report_models() -> None:
    root = get_model_root()
    print("\n==> Model status:")
    print(f"    root: {root}")
    print(f"    small models: {'OK' if small_models_present() else 'MISSING'}")
    print(f"    vlm models:   {'OK' if vlm_models_present() else 'MISSING'}")
    for tier in ("flash", "basic", "standard", "advanced"):
        ready = "ready" if models_look_complete(tier) else "incomplete"
        print(f"    tier '{tier}': {ready}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download or update local MinerU 4.x models."
    )
    parser.add_argument(
        "source",
        nargs="?",
        choices=["auto", "huggingface", "modelscope"],
        default="auto",
        help="Model download source (default: auto)",
    )
    parser.add_argument(
        "--tier",
        choices=["basic", "standard", "advanced"],
        default="standard",
        help=(
            "Which model package to download (default: standard = small + VLM; "
            "basic = small only; advanced uses the standard package)"
        ),
    )
    args = parser.parse_args()

    ensure_models_dir()
    print(f"==> Local model root: {get_model_root()}")
    print(f"==> Tier: {args.tier}")
    print(f"==> Source: {args.source}")

    # Point MinerU at the project-local config so downloads land under mineru_models/.
    write_mineru_config()

    env = os.environ.copy()
    source = _resolve_source(args.source)
    if source != "auto":
        env["MINERU_MODEL_SOURCE"] = source
    else:
        env.pop("MINERU_MODEL_SOURCE", None)

    kit = _find_mineru_kit()
    download_tier = _DOWNLOAD_TIER[args.tier]
    source_args: list[str] = [] if source == "auto" else ["--source", source]

    try:
        _run(
            [str(kit), "models", "download", "--tier", download_tier, *source_args],
            env,
        )
        _run([str(kit), "models", "verify", "--tier", download_tier], env)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: mineru-kit models command failed: {exc}", file=sys.stderr)
        return 1

    write_mineru_config()
    _report_models()

    if not models_look_complete(args.tier):
        print(
            f"\nERROR: Models for tier '{args.tier}' look incomplete after download.",
            file=sys.stderr,
        )
        return 1

    print("\n==> Models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
