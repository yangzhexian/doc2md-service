#!/usr/bin/env python3
"""Download or update local MinerU pipeline models.

Usage:
    python scripts/update.py                # auto-select source
    python scripts/update.py huggingface    # force HuggingFace
    python scripts/update.py modelscope     # force ModelScope
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from model_manager import (  # noqa: E402
    ensure_models_dir,
    get_model_root,
    get_models_dir,
    pipeline_models_look_complete,
    write_runtime_configs,
)


def _resolve_source(arg: str | None) -> str:
    if arg in ("huggingface", "modelscope"):
        return arg
    return "auto"


def _find_downloader() -> Path:
    scripts_dir = Path(sys.executable).parent
    name = "mineru-models-download.exe" if sys.platform == "win32" else "mineru-models-download"
    candidate = scripts_dir / name
    if candidate.is_file():
        return candidate
    path_binary = shutil.which("mineru-models-download")
    if path_binary is None:
        print(
            "ERROR: mineru-models-download not found. "
            "Install it with: pip install 'mineru[all]'",
            file=sys.stderr,
        )
        sys.exit(1)
    return Path(path_binary)


def _run_download(source: str, temp_config: Path) -> None:
    downloader = _find_downloader()
    cmd = [str(downloader), "-s", source, "-m", "pipeline"]
    print(f"==> Running: {' '.join(cmd)}")
    env = os.environ.copy()
    env["MINERU_TOOLS_CONFIG_JSON"] = str(temp_config)
    env.pop("MINERU_MODEL_SOURCE", None)
    subprocess.run(cmd, check=True, env=env)


def _find_downloaded_models_from_config(temp_config: Path) -> Path | None:
    if not temp_config.is_file():
        return None
    try:
        data = json.loads(temp_config.read_text(encoding="utf-8"))
    except Exception:
        return None
    models_dir = data.get("models-dir", {})
    if isinstance(models_dir, dict):
        root = models_dir.get("pipeline")
        if root:
            return Path(root).expanduser().resolve()
    if isinstance(models_dir, str):
        root = Path(models_dir).expanduser().resolve()
        if root.is_dir():
            return root
    return None


def _find_downloaded_models_by_search() -> Path | None:
    home = Path.home()
    candidates = [
        home / ".cache" / "modelscope" / "hub" / "models" / "opendatalab" / "PDF-Extract-Kit-1.0" / "models",
        home / ".cache" / "modelscope" / "hub" / "models" / "OpenDataLab" / "PDF-Extract-Kit-1___0" / "models",
        home / ".cache" / "huggingface" / "hub" / "models--opendatalab--PDF-Extract-Kit-1.0" / "snapshots",
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        if (base / "Layout").is_dir():
            return base
        for sub in base.iterdir():
            if sub.is_dir() and (sub / "Layout").is_dir():
                return sub
    return None


def _copy_models(downloaded_models: Path, target: Path) -> None:
    target_models = target / "models"
    if target_models.exists():
        print(f"==> Removing old models at {target_models}")
        shutil.rmtree(target_models)
    print(f"==> Copying {downloaded_models} -> {target_models}")
    shutil.copytree(downloaded_models, target_models)


def _report_models() -> None:
    models_dir = get_models_dir()
    print("\n==> Model layout:")
    required = {
        "Layout/PP-DocLayoutV2",
        "MFR/unimernet_hf_small_2503",
        "MFR/pp_formulanet_plus_m",
        "OCR/paddleocr_torch",
        "TabRec/SlanetPlus",
        "TabRec/UnetStructure",
        "TabCls/paddle_table_cls",
    }
    for rel in sorted(required):
        path = models_dir / rel.replace("/", os.sep)
        status = "OK" if path.exists() else "MISSING"
        print(f"    {rel}: {status}")
    ocr_dir = models_dir / "OCR" / "paddleocr_torch"
    if ocr_dir.is_dir():
        versions = {v.upper() for v in ("v6", "v5", "v4") for f in ocr_dir.rglob("*") if f.is_file() and v in f.name}
        if versions:
            print(f"    Detected OCR model versions: {', '.join(sorted(versions))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download or update local MinerU pipeline models.")
    parser.add_argument(
        "source",
        nargs="?",
        choices=["auto", "huggingface", "modelscope"],
        default="auto",
        help="Model download source (default: auto)",
    )
    args = parser.parse_args()

    target = ensure_models_dir().parent
    print(f"==> Local model root: {target}")

    if pipeline_models_look_complete():
        print("==> Existing pipeline models found; they will be replaced if the download succeeds.")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({"models-dir": {"pipeline": ""}, "model-source": "auto"}))
        temp_config = Path(tmp.name)

    try:
        _run_download(_resolve_source(args.source), temp_config)
        downloaded = _find_downloaded_models_from_config(temp_config)
        if downloaded is None:
            downloaded = _find_downloaded_models_by_search()
        if downloaded is None:
            print("ERROR: Could not locate downloaded models.", file=sys.stderr)
            return 1
        print(f"==> Found downloaded models at {downloaded}")
        _copy_models(downloaded, target)
    finally:
        temp_config.unlink(missing_ok=True)

    write_runtime_configs()
    _report_models()

    if pipeline_models_look_complete():
        print("\n==> Models are ready.")
        return 0
    print("\nERROR: Model layout looks incomplete after copy.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
