#!/usr/bin/env bash
# Download or update local MinerU models.
# Usage:
#   ./update.sh                     pipeline models, auto-select source
#   ./update.sh huggingface         force HuggingFace
#   ./update.sh modelscope          force ModelScope
#   ./update.sh auto all            pipeline + VLM models (hybrid backend)
#   ./update.sh auto vlm            VLM model only
#
# model-type: pipeline (default) | vlm | all

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
MODEL_TYPE="${2:-pipeline}"
"$PYTHON" "$SCRIPT_DIR/scripts/update.py" "$SOURCE" --model-type "$MODEL_TYPE"
