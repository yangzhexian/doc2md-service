#!/usr/bin/env python3
"""Backwards-compatible wrapper around scripts/update.py.

This file is kept for existing users/workflows. New callers should use
``python scripts/update.py`` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts_dir))

from update import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
