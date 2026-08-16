#!/usr/bin/env bash
# =========================================
# Document to Markdown Converter -- one-click start script (Linux / macOS)
# =========================================
# This script:
#   1. Creates a Python virtual environment (if missing)
#   2. Installs / upgrades dependencies when requirements.txt changes
#   3. Starts the FastAPI service at http://127.0.0.1:8000
#
# Usage:
#   ./start.sh                  # default port 8000
#   ./start.sh 9090             # custom port
#   ./start.sh --host 0.0.0.0   # listen on all interfaces
# =========================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
DEPS_FLAG="$VENV_DIR/.deps_installed"
PORT="${1:-8000}"
HOST="${2:-127.0.0.1}"

# ========== 1. Create virtual environment ==========
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "    Done."
fi

# ========== 2. Activate ==========
source "$VENV_DIR/bin/activate"

# ========== 3. Install / upgrade dependencies ==========
# The flag stores a hash of requirements.txt so that pulling a newer version
# of this repo (or bumping dependency versions) automatically reinstalls.
REQ_HASH="$("$VENV_DIR/bin/python" -c \
    "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('$SCRIPT_DIR/requirements.txt').read_bytes()).hexdigest())")"
INSTALLED_HASH=""
if [ -f "$DEPS_FLAG" ]; then
    INSTALLED_HASH="$(cat "$DEPS_FLAG" 2>/dev/null || true)"
fi
if [ "$INSTALLED_HASH" != "$REQ_HASH" ]; then
    echo "==> Installing / upgrading dependencies (this may take several minutes)..."
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
    printf '%s' "$REQ_HASH" > "$DEPS_FLAG"
    echo "    Done."
fi

# ========== 4. Check models ==========
if [ ! -d "$SCRIPT_DIR/mineru_models" ]; then
    echo ""
    echo "========== WARNING: mineru_models/ directory not found! =========="
    echo "  PDF conversion via MinerU will NOT work."
    echo "  Download models first: ./update.sh"
    echo "  (or ./update.sh modelscope if HuggingFace is inaccessible)"
    echo "=================================================================="
    echo ""
elif [ ! -d "$SCRIPT_DIR/mineru_models/vlm" ]; then
    echo ""
    echo "NOTE: VLM model not found. The high-accuracy hybrid-engine backend"
    echo "      is unavailable; falling back to the pipeline backend."
    echo "      Download it with: ./update.sh auto all"
    echo ""
fi

# ========== 5. Start service ==========
echo ""
echo "=================================================================="
echo "  Document to Markdown Converter"
echo "  Service starting at http://${HOST}:${PORT}"
echo "  API docs: http://${HOST}:${PORT}/docs"
echo "  Press Ctrl+C to stop"
echo "=================================================================="
echo ""

cd "$SCRIPT_DIR/src" && uvicorn converter_service:app --host "$HOST" --port "$PORT"
