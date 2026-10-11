"""Build bounded folder conversion plans shared by synchronous and queued batches."""

from __future__ import annotations

import os
import stat
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from resource_limits import ResourceLimits, validate_local_file


@dataclass(frozen=True)
class BatchInput:
    """One source and its output base, retaining its name within the source tree."""

    path: Path
    relative_path: Path
    output_dir: Path
    size_bytes: int


@dataclass(frozen=True)
class BatchInputPlan:
    """A fully preflighted snapshot; construction never converts or writes files."""

    folder: Path
    output_dir: Path
    recursive: bool
    items: tuple[BatchInput, ...]
    scanned_entries: int


def _select_extensions(supported: Collection[str], requested: Collection[str] | None) -> frozenset[str]:
    supported_set = frozenset(extension.lower() for extension in supported)
    if requested is None:
        return supported_set
    if isinstance(requested, str) or not requested:
        raise HTTPException(status_code=400, detail="extensions must be a nonempty list of supported file types")
    selected = set()
    for extension in requested:
        if not isinstance(extension, str) or not extension.strip():
            raise HTTPException(status_code=400, detail="extensions must contain nonempty file types")
        normalized = extension.strip().lower()
        if not normalized.startswith("."):
            normalized = "." + normalized
        if normalized not in supported_set:
            raise HTTPException(status_code=400, detail=f"Unsupported extension filter: {extension}")
        selected.add(normalized)
    return frozenset(selected)


def _is_directory_link(entry: os.DirEntry[str]) -> bool:
    """Skip symlinks and Windows directory reparse points, including junctions."""
    if entry.is_symlink():
        return True
    # Python 3.12 exposes isjunction; keep older interpreters compatible through
    # the file attributes returned by lstat-style directory entry metadata.
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is not None and is_junction(entry.path):
        return True
    if os.name == "nt":
        metadata = entry.stat(follow_symlinks=False)
        attributes = getattr(metadata, "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return False


def plan_folder_inputs(
    folder: Path,
    supported_extensions: Collection[str],
    limits: ResourceLimits,
    *,
    recursive: bool = False,
    output_dir: Path | None = None,
    extensions: Collection[str] | None = None,
) -> BatchInputPlan:
    """Preflight a flat or recursive input tree while bounding scan and file counts."""
    selected = _select_extensions(supported_extensions, extensions)
    root = folder.expanduser().resolve()
    destination = output_dir.expanduser().resolve() if output_dir is not None else root
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {root}")

    # Exclude a separate destination inside the input tree. Equal roots retain
    # normal input traversal and rely on the .docs2md namespace exclusions.
    excluded_output = None
    if destination != root and destination.is_relative_to(root):
        excluded_output = destination

    pending = [root]
    inputs: list[BatchInput] = []
    scanned = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > limits.max_folder_entries:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Folder exceeds {limits.max_folder_entries} entries: {root}",
                        )
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if not recursive:
                            continue
                        if entry.name.lower().endswith(".docs2md") or path == excluded_output:
                            continue
                        if not _is_directory_link(entry):
                            pending.append(path)
                        continue
                    # Keep file symlinks compatible with the existing flat
                    # planner. Directory symlinks do not pass is_file().
                    if path.suffix.lower() not in selected or not entry.is_file():
                        continue
                    if len(inputs) >= limits.max_batch_files:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Folder exceeds {limits.max_batch_files} supported files: {root}",
                        )
                    size = validate_local_file(path, limits)
                    relative = path.relative_to(root)
                    inputs.append(BatchInput(path, relative, destination / relative.parent, size))
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=404, detail=f"Folder not found: {directory}") from exc

    inputs.sort(key=lambda item: item.relative_path.as_posix())
    return BatchInputPlan(root, destination, recursive, tuple(inputs), scanned)
