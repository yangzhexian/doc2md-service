#!/usr/bin/env bash
# Download or update local MinerU pipeline models.
# Usage:
#   ./update.sh                auto-select source
#   ./update.sh huggingface    force HuggingFace
#   ./update.sh modelscope     force ModelScope

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -x "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
    PYTHON="venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

SOURCE="${1:-auto}"
"$PYTHON" "$SCRIPT_DIR/scripts/update.py" "$SOURCE"
