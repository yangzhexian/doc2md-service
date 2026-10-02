"""Verify portable lock policy and the dependency refresh used by both launchers."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hashing = load_module("dependency_hash", PROJECT / "scripts/requirements_hash.py")
locking = load_module("dependency_lock", PROJECT / "scripts/lock_dependencies.py")
launcher = load_module("dependency_launcher", PROJECT / "src/launcher.py")


class LockPolicyTests(unittest.TestCase):
    def test_committed_locks_match_editable_inputs(self) -> None:
        locking.check()
        manifest = json.loads((PROJECT / "requirements.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["python"], ">=3.10,<3.15")
        self.assertEqual(manifest["tested_python"], "3.12")

    def test_locks_pin_and_hash_every_requirement(self) -> None:
        for filename in ("requirements.txt", "requirements-test.txt"):
            with self.subTest(lock=filename):
                text = (PROJECT / filename).read_text(encoding="utf-8")
                self.assertIn("sys_platform", text)  # Universal, not host-specific.
                entries = text.replace("\\\n", "").splitlines()
                count = 0
                for entry in entries:
                    if not entry.strip() or entry.lstrip().startswith("#"):
                        continue
                    self.assertRegex(entry, r"^[a-zA-Z0-9_.-]+==[^\s]+")
                    self.assertRegex(entry, r"--hash=sha256:[0-9a-f]{64}")
                    self.assertNotIn(" @ ", entry)
                    count += 1
                self.assertGreater(count, 50)

    def test_test_lock_preserves_runtime_versions_and_markers(self) -> None:
        def versions(filename: str) -> set[str]:
            return {
                line.rstrip(" \\") for line in (PROJECT / filename).read_text(encoding="utf-8").splitlines()
                if re.match(r"^[a-zA-Z0-9_.-]+==", line)
            }
        self.assertLessEqual(versions("requirements.txt"), versions("requirements-test.txt"))


class DependencyHashTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docs2md_dependency_test_")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.requirements = self.project / "requirements.txt"
        self.requirements.write_text("-r nested/runtime.txt\n-c constraints.txt\n", encoding="utf-8")
        (self.project / "requirements.in").write_text("sample==1\n", encoding="utf-8")
        (self.project / "nested").mkdir()
        (self.project / "nested/runtime.txt").write_text("sample==1\n", encoding="utf-8")
        (self.project / "constraints.txt").write_text("sample==1\n", encoding="utf-8")

    def test_nested_lock_constraint_and_source_changes_refresh_installation(self) -> None:
        for filename in ("requirements.txt", "requirements.in", "nested/runtime.txt", "constraints.txt"):
            with self.subTest(filename=filename):
                before = hashing.requirements_hash(self.requirements)
                path = self.project / filename
                path.write_text(path.read_text() + "# changed\n", encoding="utf-8")
                self.assertNotEqual(before, hashing.requirements_hash(self.requirements))

    def test_nested_source_and_include_cycles_are_supported(self) -> None:
        source = self.project / "nested/runtime.in"
        source.write_text("-r ../requirements.txt\n", encoding="utf-8")
        before = hashing.requirements_hash(self.requirements)
        source.write_text(source.read_text() + "# changed\n", encoding="utf-8")
        self.assertNotEqual(before, hashing.requirements_hash(self.requirements))

    def test_git_checkout_line_endings_do_not_change_hash(self) -> None:
        before = hashing.requirements_hash(self.requirements)
        lf = self.requirements.read_bytes().replace(b"\r\n", b"\n")
        self.requirements.write_bytes(lf.replace(b"\n", b"\r\n"))
        self.assertEqual(before, hashing.requirements_hash(self.requirements))

    def test_missing_included_file_fails_before_installation(self) -> None:
        (self.project / "constraints.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            hashing.requirements_hash(self.requirements)

    def test_checkout_location_does_not_change_hash(self) -> None:
        import shutil
        moved = self.project / "copy"
        moved.mkdir()
        for filename in ("requirements.txt", "requirements.in", "constraints.txt"):
            shutil.copyfile(self.project / filename, moved / filename)
        shutil.copytree(self.project / "nested", moved / "nested")
        self.assertEqual(hashing.requirements_hash(self.requirements),
                         hashing.requirements_hash(moved / "requirements.txt"))

    def test_long_form_include_and_quoted_paths(self) -> None:
        self.requirements.write_text('--requirement="nested/runtime.txt"\n--constraint constraints.txt\n', encoding="utf-8")
        before = hashing.requirements_hash(self.requirements)
        (self.project / "constraints.txt").write_text("sample==2\n", encoding="utf-8")
        self.assertNotEqual(before, hashing.requirements_hash(self.requirements))

    def test_windows_launcher_uses_shared_recursive_hash(self) -> None:
        (self.project / "scripts").mkdir()
        import shutil
        shutil.copyfile(PROJECT / "scripts/requirements_hash.py", self.project / "scripts/requirements_hash.py")
        with patch.object(launcher, "PROJECT_DIR", self.project):
            self.assertEqual(launcher._requirements_hash(), hashing.requirements_hash(self.requirements))

    def test_windows_install_requires_hashes_and_leaves_marker_on_success(self) -> None:
        flag = self.project / ".deps_installed"
        success = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(launcher, "PROJECT_DIR", self.project), patch.object(launcher, "DEPS_FLAG", flag), \
                patch.object(launcher, "log"), patch.object(launcher.subprocess, "run", return_value=success) as run:
            launcher._install_dependencies(Path("python.exe"), "newhash")
        self.assertIn("--require-hashes", run.call_args_list[-1].args[0])
        self.assertEqual(flag.read_text(), "newhash")

    def test_windows_failed_install_preserves_previous_marker(self) -> None:
        flag = self.project / ".deps_installed"
        flag.write_text("previoushash", encoding="utf-8")
        results = [subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "hash mismatch")]
        with patch.object(launcher, "PROJECT_DIR", self.project), patch.object(launcher, "DEPS_FLAG", flag), \
                patch.object(launcher, "log"), patch.object(launcher.subprocess, "run", side_effect=results):
            with self.assertRaises(RuntimeError):
                launcher._install_dependencies(Path("python.exe"), "newhash")
        self.assertEqual(flag.read_text(), "previoushash")


if __name__ == "__main__":
    unittest.main()
