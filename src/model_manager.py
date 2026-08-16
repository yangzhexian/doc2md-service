"""Manage local MinerU models and runtime configuration files.

MinerU >= 3.4 expects a ``mineru.json`` configuration file whose
``models-dir`` maps each model kind to a local *root* directory:

- ``models-dir.pipeline`` → the directory containing the ``models/`` subtree
  (layout, formula, OCR, table models).
- ``models-dir.vlm`` → the directory containing the MinerU2.5-Pro VLM model
  used by the ``vlm-*`` and ``hybrid-*`` backends.

This module discovers the downloaded model weights and writes that config
without polluting the user's home directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_ROOT = _PROJECT_ROOT / "mineru_models"  # contains models/ subdirectory
_MODELS_DIR = _MODEL_ROOT / "models"
# MinerU >= 3.4.5 VLM backend model (MinerU2.5-Pro-2605-1.2B), stored next to
# the pipeline models so the whole mineru_models/ tree stays self-contained.
_VLM_ROOT = _MODEL_ROOT / "vlm"
_CONFIG_DIR = _PROJECT_ROOT / "config"

# Path constants matching mineru.utils.enum_class.ModelPath
_OCR_ROOT = _MODELS_DIR / "OCR" / "paddleocr_torch"


def get_project_root() -> Path:
    return _PROJECT_ROOT


def get_model_root() -> Path:
    """Return the directory that contains the ``models/`` subtree."""
    return _MODEL_ROOT


def get_models_dir() -> Path:
    return _MODELS_DIR


def get_vlm_root() -> Path:
    """Return the directory where local VLM models are (or should be) stored."""
    return _VLM_ROOT


def _discover_ocr_version() -> str:
    """Pick the newest OCR model version available locally."""
    if not _OCR_ROOT.is_dir():
        return "v4"
    files = [p.name for p in _OCR_ROOT.iterdir() if p.is_file()]
    for v in ("v6", "v5", "v4"):
        if any(v in f for f in files):
            return v
    return "v4"


def _ocr_path(pattern: str) -> str | None:
    """Return the first local OCR checkpoint matching a glob pattern, or None."""
    if not _OCR_ROOT.is_dir():
        return None
    matches = sorted(_OCR_ROOT.glob(pattern))
    if not matches:
        return None
    return str(matches[0].resolve())


def _pick_ocr_paths(version: str) -> dict[str, str | None]:
    """Return candidate OCR paths for the requested version."""
    if version == "v6":
        return {
            "det": _ocr_path("ch_PP-OCRv6_*_det_infer.*"),
            "rec": _ocr_path("ch_PP-OCRv6_*_rec_infer.*"),
            "cls": _ocr_path("ch_ptocr_mobile_v2.0_cls_infer.*"),
        }
    if version == "v5":
        return {
            "det": _ocr_path("ch_PP-OCRv5_det_infer.*"),
            "rec": _ocr_path("ch_PP-OCRv5_rec_infer.*"),
            "cls": _ocr_path("ch_ptocr_mobile_v2.0_cls_infer.*"),
        }
    # v4 defaults
    return {
        "det": _ocr_path("Multilingual_PP-OCRv3_det_infer.*"),
        "rec": _ocr_path("ch_PP-OCRv4_rec_infer.*"),
        "cls": _ocr_path("ch_ptocr_mobile_v2.0_cls_infer.*"),
    }


def _model_exists(*parts: str) -> bool:
    """Return True if a path relative to the model root exists."""
    return (_MODELS_DIR / Path(*parts)).exists()


def _min_required_models_present() -> bool:
    """Check the core pipeline models required by MinerU 3.4+."""
    layout = _model_exists("Layout", "PP-DocLayoutV2", "model.safetensors")
    mfr = _model_exists("MFR", "unimernet_hf_small_2503", "model.safetensors") or _model_exists(
        "MFR", "pp_formulanet_plus_m", "PP-FormulaNet_plus-M.pth"
    )
    ocr = _OCR_ROOT.is_dir() and any(_OCR_ROOT.iterdir())
    wired_table = _model_exists("TabRec", "UnetStructure", "unet.onnx")
    wireless_table = _model_exists("TabRec", "SlanetPlus", "slanet-plus.onnx")
    table_cls = _model_exists("TabCls", "paddle_table_cls", "PP-LCNet_x1_0_table_cls.onnx")
    return layout and mfr and ocr and wired_table and wireless_table and table_cls


def vlm_models_present() -> bool:
    """Return True if a local MinerU VLM model (MinerU2.5-Pro) is available.

    The VLM repo snapshot contains ``config.json`` plus the weights in
    safetensors / pytorch / gguf format.
    """
    if not _VLM_ROOT.is_dir():
        return False
    if not (_VLM_ROOT / "config.json").is_file():
        return False
    weight_suffixes = (".safetensors", ".bin", ".gguf")
    return any(p.is_file() and p.suffix in weight_suffixes for p in _VLM_ROOT.rglob("*"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Wrote {path}")


def build_mineru_config() -> dict[str, Any]:
    """Build the mineru.json runtime configuration for local models."""
    return {
        "model-source": "local",
        "models-dir": {
            "pipeline": str(_MODEL_ROOT.resolve()),
            "vlm": str(_VLM_ROOT.resolve()) if vlm_models_present() else "",
        },
        "config_version": "1.3.2",
    }


def write_runtime_configs() -> None:
    """Write mineru.json (and a legacy magic-pdf.json for older consumers)."""
    ocr_version = _discover_ocr_version()
    logger.info(f"Detected local OCR version: {ocr_version}")

    _write_json(_CONFIG_DIR / "mineru.json", build_mineru_config())

    # Legacy magic-pdf.json kept for backwards compatibility with magic_pdf imports.
    ocr = _pick_ocr_paths(ocr_version)
    legacy = {
        "device-mode": "cuda" if _cuda_available() else "cpu",
        "models-dir": str(_MODELS_DIR.resolve()),
        "layout-config": {"model": "DocLayout_YOLO"},
        "formula-config": {"mfr_model": "unimernet_small", "enable": True},
        "table-config": {"model": "RAPID_TABLE", "enable": True},
        "config_version": "1.0.0",
        "ocr-config": {k: v for k, v in ocr.items() if v is not None},
    }
    _write_json(_CONFIG_DIR / "magic-pdf.json", legacy)


def ensure_models_dir() -> Path:
    """Return the local models directory, creating it if necessary."""
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return _MODELS_DIR


def _cuda_available() -> bool:
    """Best-effort CUDA availability check without importing torch eagerly."""
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def pipeline_models_look_complete() -> bool:
    """Return True if the core MinerU pipeline models are present."""
    return _min_required_models_present()


def find_mineru_venv_bin() -> str | None:
    """Return the path to the venv's mineru binary, or None."""
    mineru_name = "mineru.exe" if os.name == "nt" else "mineru"
    candidate = _PROJECT_ROOT / "venv" / ("Scripts" if os.name == "nt" else "bin") / mineru_name
    if candidate.is_file():
        return str(candidate)
    return None


def run_mineru_models_download(target_dir: Path | None = None) -> int:
    """Run the official model downloader for pipeline models.

    MinerU >= 3.4.5 ships the downloader as the standalone
    ``mineru-models-download`` console script. Returns the subprocess return
    code. (Prefer ``scripts/update.py``, which additionally copies the
    downloaded weights into the project-local ``mineru_models/`` tree.)
    """
    target = target_dir or _MODELS_DIR
    target.mkdir(parents=True, exist_ok=True)

    downloader = shutil.which("mineru-models-download")
    if downloader is None:
        venv_bin = find_mineru_venv_bin()
        if venv_bin is None:
            logger.error(
                "mineru-models-download not found. Install it with: pip install 'mineru[all]'"
            )
            return 1
        downloader = str(Path(venv_bin).parent / "mineru-models-download")

    cmd = [downloader, "-s", "auto", "-m", "pipeline"]
    logger.info(f"Running model downloader: {' '.join(cmd)}")
    env = os.environ.copy()
    env["MINERU_TOOLS_CONFIG_JSON"] = str(_CONFIG_DIR / "mineru.json")
    env.pop("MINERU_MODEL_SOURCE", None)
    result = subprocess.run(
        cmd,
        env=env,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode


if __name__ == "__main__":
    write_runtime_configs()
    print(json.dumps(build_mineru_config(), indent=2))
    print("Models complete:", pipeline_models_look_complete())
