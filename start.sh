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
#   ./start.sh --port 9090 --host 0.0.0.0
#   ./start.sh --setup-only     # install dependencies without starting a server
# =========================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
DEPS_FLAG="$VENV_DIR/.deps_installed"
PORT="8000"
HOST="127.0.0.1"
SETUP_ONLY=false
POSITIONAL_COUNT=0

usage() {
    echo "Usage: $0 [port [host]] [--port PORT] [--host HOST] [--setup-only]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host|--port)
            if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "ERROR: $1 requires a value." >&2
                exit 2
            fi
            if [ "$1" = --host ]; then HOST="$2"; else PORT="$2"; fi
            shift 2
            ;;
        --host=*) HOST="${1#*=}"; shift ;;
        --port=*) PORT="${1#*=}"; shift ;;
        --setup-only) SETUP_ONLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "ERROR: Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            case "$POSITIONAL_COUNT" in
                0) PORT="$1" ;;
                1) HOST="$1" ;;
                *) echo "ERROR: Too many positional arguments." >&2; usage >&2; exit 2 ;;
            esac
            POSITIONAL_COUNT=$((POSITIONAL_COUNT + 1))
            shift
            ;;
    esac
done

if ! [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    echo "ERROR: Port must be an integer between 1 and 65535." >&2
    exit 2
fi
if [ -z "$HOST" ]; then
    echo "ERROR: Host must not be empty." >&2
    exit 2
fi

# ========== 1. Create virtual environment ==========
if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "==> Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "    Done."
fi

# ========== 2. Activate ==========
source "$VENV_DIR/bin/activate"

# ========== 3. Install / upgrade dependencies ==========
# The flag stores a hash of requirements.txt so that pulling a newer version
# of this repo (or bumping dependency versions) automatically reinstalls.
# A failed install leaves the hash unchanged so the next start retries.
REQ_HASH="$("$VENV_DIR/bin/python" -c \
    'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
    "$SCRIPT_DIR/requirements.txt")"
INSTALLED_HASH=""
if [ -f "$DEPS_FLAG" ]; then
    INSTALLED_HASH="$(cat "$DEPS_FLAG" 2>/dev/null || true)"
fi
if [ "$INSTALLED_HASH" != "$REQ_HASH" ]; then
    echo "==> Installing / upgrading dependencies (this may take several minutes)..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet
    "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
    echo "    Done."
fi

# Verify the interpreter can load uvicorn without opening a listening socket.
"$VENV_DIR/bin/python" -m uvicorn --version > /dev/null
if [ "$INSTALLED_HASH" != "$REQ_HASH" ]; then
    printf '%s' "$REQ_HASH" > "$DEPS_FLAG"
fi
if [ "$SETUP_ONLY" = true ]; then
    echo "==> Dependency setup complete."
    exit 0
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
elif [ ! -d "$SCRIPT_DIR/mineru_models/MinerU2.5-Pro-2605-1.2B" ] && \
     [ ! -d "$SCRIPT_DIR/mineru_models/MinerU2.5-Pro-2605-1.2B-GGUF" ]; then
    echo ""
    echo "NOTE: VLM model not found. The standard/advanced quality tiers"
    echo "      are unavailable; use tier=flash or tier=basic instead."
    echo "      Download it with: ./update.sh --tier standard"
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

exec "$VENV_DIR/bin/python" -m uvicorn converter_service:app \
    --app-dir "$SCRIPT_DIR/src" --host "$HOST" --port "$PORT"
