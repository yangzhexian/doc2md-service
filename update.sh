#!/usr/bin/env bash
# Download or update local MinerU 4.x models.
# Usage:
#   ./update.sh                          standard tier (small + VLM), auto source
#   ./update.sh huggingface              force HuggingFace
#   ./update.sh modelscope               force ModelScope
#   ./update.sh auto --tier basic        small models only
#   ./update.sh auto --tier standard     small + VLM (default)
#
# tier: basic | standard | advanced  (default: standard)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Redirect the model SDK caches into the project when the home directory is
# not writable (sandboxed environments, read-only home directories). The
# redirected dirs are git-ignored.
if [ ! -w "${HOME:-/nonexistent}" ] || { [ -d "${HOME:-}/.cache" ] && [ ! -w "${HOME:-}/.cache" ]; }; then
    export MODELSCOPE_HOME="$SCRIPT_DIR/.modelscope"
    export MODELSCOPE_CACHE="$SCRIPT_DIR/.cache_modelscope"
    export HF_HOME="$SCRIPT_DIR/.cache_huggingface"
fi

if [ -x "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
    PYTHON="venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

SOURCE="${1:-auto}"
shift || true
# Remaining args are forwarded (notably --tier).
"$PYTHON" "$SCRIPT_DIR/scripts/update.py" "$SOURCE" "$@"
