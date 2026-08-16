#!/usr/bin/env python3
"""Download or update local MinerU models.

MinerU >= 3.4.5 ships two model sets:
- pipeline: layout / formula / OCR / table models (PDF-Extract-Kit-1.0).
- vlm: the MinerU2.5-Pro-2605-1.2B VLM model used by the vlm-* / hybrid-*
  backends.

Usage:
    python scripts/update.py                        # pipeline models, auto source
    python scripts/update.py modelscope             # force ModelScope
    python scripts/update.py huggingface all        # pipeline + VLM models
    python scripts/update.py -m vlm                 # VLM model only
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
    get_models_dir,
    get_vlm_root,
    pipeline_models_look_complete,
    vlm_models_present,
    write_runtime_configs,
)

_PIPELINE_CACHE_CANDIDATES = [
    Path.home() / ".cache" / "modelscope" / "hub" / "models" / "opendatalab" / "PDF-Extract-Kit-1.0" / "models",
    Path.home() / ".cache" / "modelscope" / "hub" / "models" / "OpenDataLab" / "PDF-Extract-Kit-1___0" / "models",
    Path.home() / ".cache" / "huggingface" / "hub" / "models--opendatalab--PDF-Extract-Kit-1.0" / "snapshots",
]
_VLM_CACHE_CANDIDATES = [
    Path.home() / ".cache" / "modelscope" / "hub" / "models" / "OpenDataLab" / "MinerU2.5-Pro-2605-1.2B",
    Path.home() / ".cache" / "modelscope" / "hub" / "models" / "opendatalab" / "MinerU2.5-Pro-2605-1.2B",
    Path.home() / ".cache" / "huggingface" / "hub" / "models--opendatalab--MinerU2.5-Pro-2605-1.2B" / "snapshots",
]


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


def _run_download(source: str, model_type: str, temp_config: Path) -> None:
    downloader = _find_downloader()
    cmd = [str(downloader), "-s", source, "-m", model_type]
    print(f"==> Running: {' '.join(cmd)}")
    env = os.environ.copy()
    env["MINERU_TOOLS_CONFIG_JSON"] = str(temp_config)
    env.pop("MINERU_MODEL_SOURCE", None)
    subprocess.run(cmd, check=True, env=env)


def _find_downloaded_models_from_config(temp_config: Path) -> dict[str, Path | None]:
    """Read the models-dir entries the downloader wrote back into the config."""
    found: dict[str, Path | None] = {"pipeline": None, "vlm": None}
    if not temp_config.is_file():
        return found
    try:
        data = json.loads(temp_config.read_text(encoding="utf-8"))
    except Exception:
        return found
    models_dir = data.get("models-dir", {})
    if isinstance(models_dir, dict):
        for key in ("pipeline", "vlm"):
            root = models_dir.get(key)
            if root:
                path = Path(root).expanduser().resolve()
                if path.is_dir():
                    found[key] = path
    return found


def _find_downloaded_pipeline_models() -> Path | None:
    """Locate the downloaded pipeline model root in the local caches.

    Returns either the directory containing the ``models/`` subtree (MinerU
    >= 3.4.5 config layout) or the ``models/`` directory itself (legacy).
    """
    for base in _PIPELINE_CACHE_CANDIDATES:
        if not base.is_dir():
            continue
        for sub in (base, *(p for p in base.iterdir() if p.is_dir())):
            if (sub / "models" / "Layout").is_dir():
                return sub  # root containing models/
            if (sub / "Layout").is_dir():
                return sub  # the models/ directory itself
    return None


def _find_downloaded_vlm_models() -> Path | None:
    """Locate the downloaded VLM model directory (config.json at top level).

    HuggingFace stores snapshots two levels deep
    (``models--<repo>/snapshots/<hash>/``), so search with bounded depth.
    """
    for base in _VLM_CACHE_CANDIDATES:
        if not base.is_dir():
            continue
        stack: list[Path] = [base]
        for _ in range(4):  # at most 4 directory levels deep
            next_stack: list[Path] = []
            for directory in stack:
                if (directory / "config.json").is_file():
                    return directory
                next_stack.extend(p for p in directory.iterdir() if p.is_dir())
            stack = next_stack
    return None


def _copy_pipeline_models(downloaded: Path, target_models: Path) -> None:
    """Copy the pipeline models into ``<root>/models/``.

    ``downloaded`` may be either the snapshot root that contains ``models/``
    or the ``models/`` directory itself, depending on the MinerU version that
    performed the download.
    """
    source = downloaded / "models" if (downloaded / "models").is_dir() else downloaded
    if target_models.exists():
        print(f"==> Removing old models at {target_models}")
        shutil.rmtree(target_models)
    print(f"==> Copying {source} -> {target_models}")
    shutil.copytree(source, target_models)


def _copy_models(downloaded_models: Path, target: Path) -> None:
    if target.exists():
        print(f"==> Removing old models at {target}")
        shutil.rmtree(target)
    print(f"==> Copying {downloaded_models} -> {target}")
    shutil.copytree(downloaded_models, target)


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
    print(f"    VLM (MinerU2.5-Pro): {'OK' if vlm_models_present() else 'MISSING'} at {get_vlm_root()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download or update local MinerU models.")
    parser.add_argument(
        "source",
        nargs="?",
        choices=["auto", "huggingface", "modelscope"],
        default="auto",
        help="Model download source (default: auto)",
    )
    parser.add_argument(
        "-m",
        "--model-type",
        choices=["pipeline", "vlm", "all"],
        default="pipeline",
        help="Which model set to download (default: pipeline; 'all' = pipeline + VLM)",
    )
    args = parser.parse_args()

    target = ensure_models_dir().parent
    print(f"==> Local model root: {target}")
    print(f"==> Model type: {args.model_type}")

    if args.model_type in ("pipeline", "all") and pipeline_models_look_complete():
        print("==> Existing pipeline models found; they will be replaced if the download succeeds.")
    if args.model_type in ("vlm", "all") and vlm_models_present():
        print("==> Existing VLM models found; they will be replaced if the download succeeds.")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp.write(json.dumps({"models-dir": {"pipeline": "", "vlm": ""}, "model-source": "auto"}))
        temp_config = Path(tmp.name)

    try:
        _run_download(_resolve_source(args.source), args.model_type, temp_config)
        downloaded = _find_downloaded_models_from_config(temp_config)
        if not downloaded["pipeline"]:
            downloaded["pipeline"] = _find_downloaded_pipeline_models()
        if not downloaded["vlm"]:
            downloaded["vlm"] = _find_downloaded_vlm_models()

        if args.model_type in ("pipeline", "all"):
            if downloaded["pipeline"] is None:
                print("ERROR: Could not locate downloaded pipeline models.", file=sys.stderr)
                return 1
            print(f"==> Found downloaded pipeline models at {downloaded['pipeline']}")
            _copy_pipeline_models(downloaded["pipeline"], target / "models")

        if args.model_type in ("vlm", "all"):
            if downloaded["vlm"] is None:
                print("ERROR: Could not locate downloaded VLM models.", file=sys.stderr)
                return 1
            print(f"==> Found downloaded VLM models at {downloaded['vlm']}")
            _copy_models(downloaded["vlm"], get_vlm_root())
    finally:
        temp_config.unlink(missing_ok=True)

    write_runtime_configs()
    _report_models()

    if args.model_type in ("pipeline", "all") and not pipeline_models_look_complete():
        print("\nERROR: Pipeline model layout looks incomplete after copy.", file=sys.stderr)
        return 1
    if args.model_type in ("vlm", "all") and not vlm_models_present():
        print("\nERROR: VLM model layout looks incomplete after copy.", file=sys.stderr)
        return 1

    print("\n==> Models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
