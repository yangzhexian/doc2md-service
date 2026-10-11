"""Folder plans preflight recursive work and preserve relative output paths."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import HTTPException  # noqa: E402

from batch_inputs import plan_folder_inputs  # noqa: E402
from resource_limits import ResourceLimits, collect_folder_inputs  # noqa: E402


class BatchInputPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="docs2md_batch_inputs_")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.root = self.workspace / "inputs"
        self.root.mkdir()
        self.limits = ResourceLimits(max_file_bytes=8, max_batch_files=10, max_folder_entries=50)
        self.supported = {".txt", ".pdf", ".csv"}

    def write(self, relative: str, content: bytes = b"text") -> Path:
        source = self.root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        return source

    def test_default_flat_plan_matches_existing_collector(self) -> None:
        self.write("b.txt")
        self.write("a.csv")
        self.write("nested/c.txt")
        self.write("ignore.bin")
        plan = plan_folder_inputs(self.root, self.supported, self.limits)
        self.assertFalse(plan.recursive)
        self.assertEqual(plan.folder, self.root)
        self.assertEqual(plan.output_dir, self.root)
        self.assertEqual([item.path for item in plan.items], collect_folder_inputs(self.root, self.supported, self.limits))
        self.assertEqual([item.relative_path for item in plan.items], [Path("a.csv"), Path("b.txt")])
        self.assertEqual([item.output_dir for item in plan.items], [self.root, self.root])
        self.assertEqual(plan.scanned_entries, 4)

    def test_recursive_same_names_keep_separate_output_parents(self) -> None:
        self.write("east/report.txt", b"east")
        self.write("west/report.txt", b"west")
        self.write("west/deep/report.txt", b"deep")
        output = self.workspace / "output"
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True, output_dir=output)
        self.assertTrue(plan.recursive)
        self.assertEqual(plan.output_dir, output)
        self.assertEqual([item.relative_path.as_posix() for item in plan.items], ["east/report.txt", "west/deep/report.txt", "west/report.txt"])
        self.assertEqual([item.output_dir for item in plan.items], [output / "east", output / "west/deep", output / "west"])
        self.assertEqual([item.size_bytes for item in plan.items], [4, 4, 4])
        self.assertFalse(output.exists(), "planning must not create conversion output")

    def test_recursive_default_output_retains_source_parent_layout(self) -> None:
        source = self.write("nested/note.txt")
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual(plan.items[0].path, source)
        self.assertEqual(plan.items[0].output_dir, source.parent)

    def test_generated_output_trees_are_excluded_without_scanning_contents(self) -> None:
        self.write("original.txt")
        self.write("original.txt.docs2md/ocr.txt")
        self.write("nested/report.PDF.DOCS2MD/temporary.pdf")
        self.write("nested/original.csv")
        limits = ResourceLimits(max_file_bytes=8, max_batch_files=2, max_folder_entries=5)
        plan = plan_folder_inputs(self.root, self.supported, limits, recursive=True)
        self.assertEqual([item.relative_path.as_posix() for item in plan.items], ["nested/original.csv", "original.txt"])
        self.assertEqual(plan.scanned_entries, 5)

    def test_nested_distinct_output_tree_is_excluded(self) -> None:
        self.write("nested/source.txt")
        self.write("nested/output/generated.txt")
        self.write("nested/output/deep/too-many.txt")
        output = self.root / "nested/output"
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True, output_dir=output)
        self.assertEqual([item.relative_path.as_posix() for item in plan.items], ["nested/source.txt"])
        self.assertEqual(plan.items[0].output_dir, output / "nested")
        self.assertEqual(plan.scanned_entries, 3)

    def test_output_equal_to_input_root_does_not_prune_inputs(self) -> None:
        self.write("nested/source.txt")
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True, output_dir=self.root)
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].relative_path, Path("nested/source.txt"))

    def test_extension_filter_normalizes_and_deduplicates_types(self) -> None:
        self.write("report.PDF")
        self.write("inventory.csv")
        self.write("ignored.txt")
        limits = ResourceLimits(max_file_bytes=8, max_batch_files=2, max_folder_entries=3)
        plan = plan_folder_inputs(self.root, self.supported, limits, extensions=[" pdf ", ".PDF", "CSV"])
        self.assertEqual([item.relative_path.name for item in plan.items], ["inventory.csv", "report.PDF"])

    def test_invalid_filters_reject_before_scanning(self) -> None:
        for extensions in ([], [""], [" "], [".unknown"], ["pdf", "exe"], ["*"], "pdf", [None]):
            with self.subTest(extensions=extensions):
                with patch("batch_inputs.os.scandir") as scan:
                    with self.assertRaises(HTTPException) as invalid:
                        plan_folder_inputs(self.root, self.supported, self.limits, extensions=extensions)
                self.assertEqual(invalid.exception.status_code, 400)
                scan.assert_not_called()

    def test_file_limit_applies_across_subfolders_before_plan_is_returned(self) -> None:
        self.write("left/a.txt")
        self.write("right/b.txt")
        self.write("right/c.txt")
        limits = ResourceLimits(max_file_bytes=8, max_batch_files=2, max_folder_entries=50)
        with self.assertRaises(HTTPException) as excessive:
            plan_folder_inputs(self.root, self.supported, limits, recursive=True)
        self.assertEqual(excessive.exception.status_code, 413)
        self.assertIn("2 supported files", excessive.exception.detail)
        self.assertFalse(list(self.root.rglob("*.docs2md")))

    def test_file_size_preflight_checks_every_selected_input(self) -> None:
        self.write("small.txt")
        large = self.write("nested/large.pdf", b"123456789")
        with self.assertRaises(HTTPException) as excessive:
            plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual(excessive.exception.status_code, 413)
        self.assertIn(str(large), excessive.exception.detail)
        self.assertFalse(list(self.root.rglob("*.docs2md")))

    def test_unselected_large_files_do_not_consume_file_or_size_budget(self) -> None:
        self.write("large.txt", b"123456789")
        self.write("selected.pdf", b"12345678")
        limits = ResourceLimits(max_file_bytes=8, max_batch_files=1, max_folder_entries=2)
        plan = plan_folder_inputs(self.root, self.supported, limits, extensions=["pdf"])
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(plan.items[0].size_bytes, 8)

    def test_entry_limit_is_cumulative_across_the_recursive_tree(self) -> None:
        self.write("left/one.bin")
        self.write("right/two.bin")
        limits = ResourceLimits(max_file_bytes=8, max_batch_files=10, max_folder_entries=3)
        with self.assertRaises(HTTPException) as excessive:
            plan_folder_inputs(self.root, self.supported, limits, recursive=True)
        self.assertEqual(excessive.exception.status_code, 413)
        self.assertIn("3 entries", excessive.exception.detail)

    def test_scan_stops_immediately_after_first_excess_entry(self) -> None:
        visited = []

        class Entry:
            name = "ignored.bin"
            path = str(self.root / name)

            def is_dir(self, *, follow_symlinks=True):
                return False

        def entries():
            for index in range(100):
                visited.append(index)
                yield Entry()

        class Scan:
            def __enter__(self):
                return entries()

            def __exit__(self, *exc):
                return False

        limits = ResourceLimits(max_file_bytes=8, max_batch_files=10, max_folder_entries=3)
        with patch("batch_inputs.os.scandir", return_value=Scan()):
            with self.assertRaises(HTTPException):
                plan_folder_inputs(self.root, self.supported, limits, recursive=True)
        self.assertEqual(visited, [0, 1, 2, 3])

    def test_missing_and_file_roots_report_not_found(self) -> None:
        source = self.write("note.txt")
        for folder in (source, self.root / "missing"):
            with self.subTest(folder=folder):
                with self.assertRaises(HTTPException) as missing:
                    plan_folder_inputs(folder, self.supported, self.limits)
                self.assertEqual(missing.exception.status_code, 404)

    def test_empty_folder_produces_empty_valid_plan(self) -> None:
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual(plan.items, ())
        self.assertEqual(plan.scanned_entries, 0)

    def test_file_symlink_alias_and_relative_name_are_preserved(self) -> None:
        target = self.workspace / "outside.txt"
        target.write_bytes(b"outside")
        alias = self.root / "alias.txt"
        try:
            alias.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"File symlinks unavailable: {exc}")
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual(plan.items[0].path, alias)
        self.assertEqual(plan.items[0].relative_path, Path("alias.txt"))
        self.assertEqual(plan.items[0].output_dir, self.root)
        self.assertEqual(plan.items[0].size_bytes, 7)

    def test_directory_symlinks_and_cycles_are_not_traversed(self) -> None:
        self.write("nested/source.txt")
        external = self.workspace / "external"
        external.mkdir()
        (external / "outside.txt").write_bytes(b"outside")
        try:
            (self.root / "external.txt").symlink_to(external, target_is_directory=True)
            (self.root / "nested/cycle").symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symlinks unavailable: {exc}")
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual([item.relative_path.as_posix() for item in plan.items], ["nested/source.txt"])
        self.assertEqual(plan.scanned_entries, 4)

    @unittest.skipUnless(os.name == "nt", "Windows directory junction behavior")
    def test_windows_junction_is_not_traversed(self) -> None:
        self.write("source.txt")
        external = self.workspace / "external"
        external.mkdir()
        (external / "outside.txt").write_bytes(b"outside")
        junction = self.root / "junction"
        command = ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            self.skipTest(f"Directory junctions unavailable: {result.stderr}")
        self.addCleanup(junction.rmdir)
        plan = plan_folder_inputs(self.root, self.supported, self.limits, recursive=True)
        self.assertEqual([item.relative_path.as_posix() for item in plan.items], ["source.txt"])
        self.assertEqual(plan.scanned_entries, 2)


if __name__ == "__main__":
    unittest.main()
