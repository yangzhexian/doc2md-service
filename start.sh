#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Document to Markdown Converter — one-click start script (Linux / macOS)
# ─────────────────────────────────────────────────────────────────────────────
# This script:
#   1. Creates a Python virtual environment (if missing)
#   2. Installs dependencies (if not already done)
#   3. Starts the FastAPI service at http://127.0.0.1:8000
#
# Usage:
#   ./start.sh                  # default port 8000
#   ./start.sh 9090             # custom port
#   ./start.sh --host 0.0.0.0   # listen on all interfaces
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
DEPS_FLAG="$VENV_DIR/.deps_installed"
PORT="${1:-8000}"
HOST="${2:-127.0.0.1}"

# ── 1. Create virtual environment ───────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "    Done."
fi

# ── 2. Activate ──────────────────────────────────────────────────────────
source "$VENV_DIR/bin/activate"

# ── 3. Install dependencies ─────────────────────────────────────────────
if [ ! -f "$DEPS_FLAG" ]; then
    echo "==> Installing dependencies (this may take several minutes)..."
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
    touch "$DEPS_FLAG"
    echo "    Done."
fi

# ── 4. Check models ──────────────────────────────────────────────────────
if [ ! -d "$SCRIPT_DIR/mineru_models" ]; then
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║  WARNING: mineru_models/ directory not found!                 ║"
    echo "║  PDF conversion via MinerU will NOT work.                     ║"
    echo "║  Download models first: mineru-models-download                ║"
    echo "║  Then copy them to: $SCRIPT_DIR/mineru_models/                ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
fi

# ── 5. Start service ────────────────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Document to Markdown Converter                               ║"
echo "║  Service starting at http://${HOST}:${PORT}                         ║"
echo "║  API docs: http://${HOST}:${PORT}/docs                            ║"
echo "║  Press Ctrl+C to stop                                         ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

uvicorn converter_service:app --host "$HOST" --port "$PORT"
