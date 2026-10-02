"""Model readiness regressions using temporary files and MinerU metadata."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model_manager  # noqa: E402
from mineru.config import config  # noqa: E402
from mineru.model.registry import (  # noqa: E402
    MINERU_4_MODELS_ONNX as ONNX,
    MINERU_4_MODELS_TORCH as TORCH,
    MINERU_2_5_PRO_2605_1_2B as VLM,
    MINERU_2_5_PRO_2605_1_2B_GGUF as GGUF,
)


def write_file(path: Path, data: bytes = b"payload") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def complete_package(root: Path, repo) -> Path:
    package = root / repo.name
    package.mkdir(parents=True, exist_ok=True)
    for relative in repo.paths.values():
        path = package / relative
        if path.suffix:
            write_file(path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / ".mineru_complete").touch()
    if repo.download_mode == "full":
        (package / ".mineru_complete").touch()
    if repo is TORCH:
        layout = package / repo.paths["pp_doclayout_v2"]
        for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
            write_file(layout / name)
        ocr = package / repo.paths["pytorch_paddle"]
        for name in (
            "ch_PP-OCRv6_tiny_det_infer.safetensors",
            "ch_PP-OCRv6_small_rec_infer.safetensors",
            "seal_PP-OCRv4_det_infer.pth",
        ):
            write_file(ocr / name)
    if repo is VLM:
        for name in (
            "config.json", "tokenizer.json", "tokenizer_config.json",
            "preprocessor_config.json", "model.safetensors",
        ):
            write_file(package / name)
    return package


class ModelReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        # Model locations resolve Windows 8.3 aliases before returning paths.
        self.root = Path(temp_dir.name).resolve()
        self.env = patch.dict(os.environ, {
            "MINERU_MODEL_BASE_DIR": str(self.root),
            "MINERU_MODEL_SMALL_BACKEND": "auto",
            "MINERU_MODEL_VLM_ENGINE": "auto",
            "DOCS2MD_MINERU_VLM_ENGINE": "auto",
            "MINERU_DEVICE_MODE": "cpu",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        device = patch("mineru.model.runtime.device.get_device", return_value="cpu")
        self.device = device.start()
        self.addCleanup(device.stop)
        modules = patch("mineru.model.runtime.device.module_available", return_value=True)
        self.modules = modules.start()
        self.addCleanup(modules.stop)
        vlm_device = patch("mineru.model.vlm.selector.get_device", return_value="cpu")
        self.vlm_device = vlm_device.start()
        self.addCleanup(vlm_device.stop)
        vlm_modules = patch("mineru.model.vlm.selector.module_available", return_value=True)
        vlm_modules.start()
        self.addCleanup(vlm_modules.stop)

    def test_complete_selected_packages_are_ready_without_downloads(self) -> None:
        complete_package(self.root, ONNX)
        complete_package(self.root, GGUF)
        original_root = config.model.base_dir
        with patch("mineru.model.download.download_model_repo") as download_repo, patch(
            "mineru.model.download.download_model_files"
        ) as download_files:
            for tier in ("flash", "basic", "standard", "advanced"):
                self.assertTrue(model_manager.models_look_complete(tier), tier)
            download_repo.assert_not_called()
            download_files.assert_not_called()
        self.assertEqual(config.model.base_dir, original_root)
        self.assertEqual(model_manager.get_vlm_root(), self.root / GGUF.name)

    def test_cpu_does_not_accept_complete_torch_or_full_vlm_packages(self) -> None:
        complete_package(self.root, TORCH)
        complete_package(self.root, VLM)
        self.assertFalse(model_manager.small_models_present())
        self.assertFalse(model_manager.vlm_models_present())

    def test_gpu_selects_torch_and_original_vlm_instead_of_cpu_packages(self) -> None:
        complete_package(self.root, ONNX)
        complete_package(self.root, GGUF)
        self.device.return_value = "cuda"
        self.vlm_device.return_value = "cuda"
        self.assertFalse(model_manager.small_models_present())
        self.assertFalse(model_manager.vlm_models_present())
        complete_package(self.root, TORCH)
        complete_package(self.root, VLM)
        self.assertTrue(model_manager.small_models_present())
        self.assertTrue(model_manager.vlm_models_present())

    def test_single_weight_is_not_a_complete_package(self) -> None:
        write_file(self.root / ONNX.name / "only.onnx")
        self.assertFalse(model_manager.models_look_complete("basic"))

    def test_every_registered_onnx_path_must_be_nonempty(self) -> None:
        package = complete_package(self.root, ONNX)
        self.assertTrue(model_manager.small_models_present())
        for relative in ONNX.paths.values():
            with self.subTest(relative=relative):
                write_file(package / relative, b"")
                self.assertFalse(model_manager.small_models_present())
                write_file(package / relative)

    def test_torch_directory_markers_and_runtime_files_are_required(self) -> None:
        os.environ["MINERU_MODEL_SMALL_BACKEND"] = "torch"
        package = complete_package(self.root, TORCH)
        self.assertTrue(model_manager.small_models_present())
        layout = package / TORCH.paths["pp_doclayout_v2"]
        marker = layout / ".mineru_complete"
        marker.unlink()
        self.assertFalse(model_manager.small_models_present())
        marker.touch()
        (layout / "config.json").unlink()
        self.assertFalse(model_manager.small_models_present())

    def test_gguf_requires_exact_names_complete_marker_and_nonempty_projector(self) -> None:
        package = self.root / GGUF.name
        write_file(package / "model.gguf")
        write_file(package / "mmproj-model.gguf")
        (package / ".mineru_complete").touch()
        self.assertFalse(model_manager.vlm_models_present())
        complete_package(self.root, GGUF)
        self.assertTrue(model_manager.vlm_models_present())
        write_file(package / GGUF.paths["mmproj"], b"")
        self.assertFalse(model_manager.vlm_models_present())
        write_file(package / GGUF.paths["mmproj"])
        (package / ".mineru_complete").unlink()
        self.assertFalse(model_manager.vlm_models_present())

    def test_full_vlm_requires_metadata_and_every_declared_shard(self) -> None:
        os.environ["MINERU_MODEL_VLM_ENGINE"] = "lmdeploy"
        package = complete_package(self.root, VLM)
        self.assertTrue(model_manager.vlm_models_present())
        (package / "tokenizer.json").unlink()
        self.assertFalse(model_manager.vlm_models_present())
        write_file(package / "tokenizer.json")
        (package / "model.safetensors").unlink()
        write_file(package / "model.safetensors.index.json", json.dumps({
            "weight_map": {"a": "part-1.safetensors", "b": "part-2.safetensors"},
        }).encode())
        write_file(package / "part-1.safetensors")
        self.assertFalse(model_manager.vlm_models_present())
        write_file(package / "part-2.safetensors")
        self.assertTrue(model_manager.vlm_models_present())
        write_file(package / "part-2.safetensors", b"")
        self.assertFalse(model_manager.vlm_models_present())

    def test_missing_dependencies_or_invalid_configuration_return_false(self) -> None:
        complete_package(self.root, ONNX)
        complete_package(self.root, GGUF)
        self.modules.return_value = False
        self.assertFalse(model_manager.small_models_present())
        self.assertFalse(model_manager.vlm_models_present())
        self.modules.return_value = True
        os.environ["MINERU_MODEL_SMALL_BACKEND"] = "unknown"
        os.environ["MINERU_MODEL_VLM_ENGINE"] = "unknown"
        self.assertFalse(model_manager.small_models_present())
        self.assertFalse(model_manager.vlm_models_present())
        self.assertFalse(model_manager.models_look_complete("unknown"))
        self.assertTrue(model_manager.models_look_complete("flash"))


if __name__ == "__main__":
    unittest.main()
