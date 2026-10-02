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

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from mineru.model.download import ModelRepo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_ROOT = _PROJECT_ROOT / "mineru_models"
_CONFIG_DIR = _PROJECT_ROOT / "config"

_WEIGHT_SUFFIXES = (".onnx", ".pth", ".safetensors", ".bin", ".pt", ".gguf", ".mnn")


def get_project_root() -> Path:
    return _PROJECT_ROOT


def get_model_root() -> Path:
    return _MODEL_ROOT


def get_vlm_root() -> Path:
    """Return the VLM package dir for the selected local backend."""
    from mineru.model.registry import vlm_model_repo

    selected = os.environ.get("MINERU_MODEL_VLM_ENGINE", get_vlm_engine())
    return _local_repo(vlm_model_repo(selected)).local_dir()


def ensure_models_dir() -> Path:
    """Return the local models directory, creating it if necessary."""
    _MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    return _MODEL_ROOT


def _local_repo(repo: ModelRepo, path: Path | None = None) -> ModelRepo:
    """Bind registry metadata to our runtime root without changing MinerU config.

    MinerU joins ``base_dir / local_name``; an absolute local_name lets its
    public verifier inspect this tree even if its config singleton was loaded
    before the service wrote config/mineru.yaml.
    """
    if path is None:
        root = Path(os.environ.get("MINERU_MODEL_BASE_DIR", str(_MODEL_ROOT))).expanduser()
        path = root / repo.local_name
    return replace(repo, local_name=str(path.resolve()))


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _safetensors_present(path: Path) -> bool:
    """Check a single weight or every shard listed by a safetensors index."""
    index = path / "model.safetensors.index.json"
    if not index.exists():
        return _nonempty_file(path / "model.safetensors")
    manifest = json.loads(index.read_text(encoding="utf-8"))
    weight_map = manifest.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        return False
    for name in set(weight_map.values()):
        if not isinstance(name, str) or not name:
            return False
        shard = (path / name).resolve()
        if not shard.is_relative_to(path.resolve()) or not _nonempty_file(shard):
            return False
    return True


def _repo_present(repo: ModelRepo) -> bool:
    from mineru.model.download import verify_model_repo

    root = repo.local_dir()
    if not verify_model_repo(repo).ready:
        return False
    for resource in repo.required_paths():
        path = resource.local_path()
        if path.is_file():
            if not _nonempty_file(path):
                return False
        elif path.is_dir():
            weights = [
                p for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in _WEIGHT_SUFFIXES
            ]
            if not weights or not all(_nonempty_file(p) for p in weights):
                return False
        else:
            return False

    # The Torch registry declares these directories as complete snapshots.
    # Check their runtime files too, so a stale marker cannot hide missing data.
    if repo.name == "MinerU-4_models_torch":
        layout = root / repo.paths["pp_doclayout_v2"]
        ocr = root / repo.paths["pytorch_paddle"]
        required = (
            layout / "config.json", layout / "preprocessor_config.json",
            ocr / "ch_PP-OCRv6_tiny_det_infer.safetensors",
            ocr / "ch_PP-OCRv6_small_rec_infer.safetensors",
            ocr / "seal_PP-OCRv4_det_infer.pth",
        )
        return all(_nonempty_file(p) for p in required) and _safetensors_present(layout)
    if repo.name == "MinerU2.5-Pro-2605-1.2B":
        required = (
            "config.json", "tokenizer.json", "tokenizer_config.json",
            "preprocessor_config.json",
        )
        return all(_nonempty_file(root / name) for name in required) and _safetensors_present(root)
    return True


def _package_present(path: Path) -> bool:
    """Verify a known package, including exact runtime paths and nonempty data."""
    try:
        from mineru.model.registry import get_model_repo

        return _repo_present(_local_repo(get_model_repo(path.name), path))
    except (ImportError, OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
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
    """True when the actual small-model backend has complete local models."""
    try:
        from mineru.model.registry import small_model_repo
        from mineru.model.runtime.device import TORCH_REQUIRED_MODULES, module_available

        selected = os.environ.get("MINERU_MODEL_SMALL_BACKEND", "auto")
        repo = small_model_repo(selected)
        modules = ("onnxruntime",)
        if repo.name == "MinerU-4_models_torch":
            modules += TORCH_REQUIRED_MODULES
        return (
            all(module_available(name) for name in modules)
            and _repo_present(_local_repo(repo))
        )
    except (ImportError, OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return False


def vlm_models_present() -> bool:
    """True when the resolved VLM engine has its complete model package."""
    try:
        from mineru.model.registry import vlm_model_repo
        from mineru.model.runtime.device import module_available
        from mineru.model.vlm.selector import VLM_REQUIRED_MODULES, resolve_vlm_engine

        selected = os.environ.get("MINERU_MODEL_VLM_ENGINE", get_vlm_engine())
        engine = resolve_vlm_engine(selected)
        modules = VLM_REQUIRED_MODULES[engine]
        return (
            all(module_available(name) for name in modules)
            and _repo_present(_local_repo(vlm_model_repo(engine)))
        )
    except (ImportError, OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return False


def models_look_complete(tier: str = "standard") -> bool:
    """Return True when the models required by ``tier`` are present.

    - ``flash``: no local models required.
    - ``basic``: small models.
    - ``standard`` / ``advanced``: small models + VLM (same package).
    """
    tier = (tier or "standard").strip().lower()
    if tier not in {"flash", "basic", "standard", "advanced"}:
        return False
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


if __name__ == "__main__":
    path = write_mineru_config()
    print(f"Config: {path}")
    print(build_mineru_config_text())
    for tier in ("flash", "basic", "standard", "advanced"):
        print(f"models_look_complete({tier!r}) = {models_look_complete(tier)}")
