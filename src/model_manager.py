"""Manage local MinerU 4.x models and runtime configuration.

MinerU >= 4.0 reads ``$MINERU_HOME/config.yaml`` (overridable via
``MINERU_CONFIG``) instead of the legacy ``config/mineru.json``. This module
writes a project-local ``config/mineru.yaml`` pointing at the self-contained
``mineru_models/`` tree and probes that tree for the packages each quality
tier needs.

Layout under ``mineru_models/`` (see ``mineru-kit models show``):

- ``MinerU-4_models_torch`` / ``MinerU-4_models_onnx`` — small models
- ``MinerU2.5-Pro-2605-1.2B`` / ``...-GGUF`` — VLM models

Tier requirements:

- ``flash``: none
- ``basic``: one small-model package
- ``standard`` / ``advanced``: small models + one VLM package
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_ROOT = _PROJECT_ROOT / "mineru_models"
_CONFIG_DIR = _PROJECT_ROOT / "config"

_SMALL_PACKAGE_DIRS = (
    _MODEL_ROOT / "MinerU-4_models_torch",
    _MODEL_ROOT / "MinerU-4_models_onnx",
)
_VLM_PACKAGE_DIRS = (
    _MODEL_ROOT / "MinerU2.5-Pro-2605-1.2B",
    _MODEL_ROOT / "MinerU2.5-Pro-2605-1.2B-GGUF",
)
_WEIGHT_SUFFIXES = (".onnx", ".pth", ".safetensors", ".bin", ".pt", ".gguf", ".mnn")


def get_project_root() -> Path:
    return _PROJECT_ROOT


def get_model_root() -> Path:
    return _MODEL_ROOT


def get_vlm_root() -> Path:
    """Return the VLM package dir for the selected local backend."""
    if get_vlm_engine() == "llama-cpp":
        return _VLM_PACKAGE_DIRS[1]
    for candidate in _VLM_PACKAGE_DIRS:
        if candidate.is_dir():
            return candidate
    return _VLM_PACKAGE_DIRS[0]


def ensure_models_dir() -> Path:
    """Return the local models directory, creating it if necessary."""
    _MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    return _MODEL_ROOT


def _package_present(path: Path) -> bool:
    if not path.is_dir():
        return False
    for p in path.rglob("*"):
        if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() in _WEIGHT_SUFFIXES:
            return True
    return False


def get_vlm_engine() -> str:
    """Choose a local VLM backend that works with the bundled model package.

    MinerU 4.0.5's LMDeploy path can fail while dispatching the vision model on
    Windows. Its llama.cpp backend uses the GGUF package and works there.
    Other platforms keep MinerU's own automatic backend selection.
    """
    engine = os.environ.get("DOCS2MD_MINERU_VLM_ENGINE", "").strip().lower()
    if not engine:
        return "llama-cpp" if sys.platform == "win32" else "auto"
    if engine not in {"auto", "llama-cpp", "lmdeploy", "vllm", "mlx"}:
        raise ValueError(f"Invalid DOCS2MD_MINERU_VLM_ENGINE: {engine}")
    return engine


def small_models_present() -> bool:
    """True when a MinerU 4.x small-model package is installed."""
    return any(_package_present(p) for p in _SMALL_PACKAGE_DIRS)


def vlm_models_present() -> bool:
    """True when a MinerU VLM model package is installed."""
    engine = get_vlm_engine()
    if engine == "llama-cpp":
        gguf_dir = _VLM_PACKAGE_DIRS[1]
        return (
            any(p.stat().st_size > 0 for p in gguf_dir.glob("*.gguf") if not p.name.startswith("mmproj-"))
            and any(p.stat().st_size > 0 for p in gguf_dir.glob("mmproj-*.gguf"))
        )
    if engine in {"lmdeploy", "vllm", "mlx"}:
        return _package_present(_VLM_PACKAGE_DIRS[0])
    return any(_package_present(p) for p in _VLM_PACKAGE_DIRS)


def models_look_complete(tier: str = "standard") -> bool:
    """Return True when the models required by ``tier`` are present.

    - ``flash``: no local models required.
    - ``basic``: small models.
    - ``standard`` / ``advanced``: small models + VLM (same package).
    """
    tier = (tier or "standard").strip().lower()
    if tier == "flash":
        return True
    if not small_models_present():
        return False
    if tier in ("standard", "advanced"):
        return vlm_models_present()
    return True


def build_mineru_config_text() -> str:
    """Build the MinerU 4.x ``config.yaml`` body for project-local models."""
    base_dir = _MODEL_ROOT.resolve().as_posix()
    return (
        "model:\n"
        "  source: local\n"
        f'  base_dir: "{base_dir}"\n'
        "  small_backend: auto\n"
        "  vlm:\n"
        f"    engine: {get_vlm_engine()}\n"
    )


def write_mineru_config() -> Path:
    """Write ``config/mineru.yaml`` and point ``MINERU_CONFIG`` at it.

    Returns the config path. Safe to call multiple times; intended to run once
    at service startup rather than per conversion.
    """
    config_path = _CONFIG_DIR / "mineru.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(build_mineru_config_text(), encoding="utf-8")
    os.environ["MINERU_CONFIG"] = str(config_path.resolve())
    logger.info(f"Wrote {config_path} (MINERU_CONFIG={config_path})")
    return config_path


def _cuda_available() -> bool:
    """Best-effort CUDA availability check without importing torch eagerly."""
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    path = write_mineru_config()
    print(f"Config: {path}")
    print(build_mineru_config_text())
    for tier in ("flash", "basic", "standard", "advanced"):
        print(f"models_look_complete({tier!r}) = {models_look_complete(tier)}")
