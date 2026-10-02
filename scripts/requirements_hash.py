"""Hash dependency locks and their local inputs, including nested requirements.

Both launchers use this helper so an included lock or its editable .in source
invalidates the installation marker just as changing requirements.txt does.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path


_INCLUDE = re.compile(r"^(?:-r\s*|--requirement(?:=|\s+)|-c\s*|--constraint(?:=|\s+))(.+)$")


def requirements_hash(requirements: Path) -> str:
    requirements = requirements.resolve()
    files: dict[Path, bytes] = {}

    def visit(path: Path) -> None:
        path = path.resolve()
        if path in files:
            return
        content = path.read_bytes().replace(b"\r\n", b"\n")
        files[path] = content
        # Generated .txt locks have a matching editable input. The lock itself
        # is flattened, so pip does not retain a reference to this source.
        if path.suffix == ".txt" and path.with_suffix(".in").is_file():
            visit(path.with_suffix(".in"))
        logical = content.decode("utf-8").replace("\\\r\n", "").replace("\\\n", "")
        for line in logical.splitlines():
            match = _INCLUDE.match(line.strip().split(" #", 1)[0])
            if match:
                target = match.group(1).strip().strip("\"'")
                if "://" in target:
                    raise ValueError("Dependency inputs must be local files: " + target)
                visit(path.parent / target)

    visit(requirements)
    digest = hashlib.sha256()
    # Include file names and sizes so concatenated file contents are unambiguous
    # and relocating the whole checkout produces the same installation marker.
    for path, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
        name = Path(os.path.relpath(path, requirements.parent)).as_posix()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(len(content)).encode("ascii") + b"\0" + content)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requirements", type=Path)
    print(requirements_hash(parser.parse_args().requirements))
