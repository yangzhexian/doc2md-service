#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Install docs2md as a systemd user service (starts on login)
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   chmod +x scripts/install-autostart.sh
#   ./scripts/install-autostart.sh              # default port 8000
#   ./scripts/install-autostart.sh 9090         # custom port
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${1:-8000}"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="docs2md"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

echo "==> Installing docs2md autostart service..."
echo "    Project dir : $PROJECT_DIR"
echo "    Venv dir    : $VENV_DIR"
echo "    Port        : $PORT"

# ── 1. Check prerequisites ────────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/converter_service.py" ]; then
    echo "ERROR: converter_service.py not found in $PROJECT_DIR"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Virtual environment not found. Running start.sh first to set up..."
    cd "$PROJECT_DIR"
    bash start.sh "$PORT" &
    sleep 5
    # start.sh creates the venv — wait a bit then kill the temporary service
    kill %1 2>/dev/null || true
    if [ ! -d "$VENV_DIR" ]; then
        echo "ERROR: Failed to create virtual environment."
        exit 1
    fi
fi

# ── 2. Create systemd user directory if needed ────────────────────────────
mkdir -p "$HOME/.config/systemd/user"

# ── 3. Generate service file from template ─────────────────────────────────
echo "==> Creating systemd user service file..."
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__VENV_DIR__|$VENV_DIR|g" \
    -e "s|__PORT__|$PORT|g" \
    "$SCRIPT_DIR/docs2md.service" > "$SERVICE_FILE"

echo "    Service file: $SERVICE_FILE"

# ── 4. Enable and start the service ────────────────────────────────────────
echo "==> Enabling and starting the service..."
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service"
systemctl --user start "$SERVICE_NAME.service"

# ── 5. Check status ────────────────────────────────────────────────────────
echo ""
echo "==> Service status:"
systemctl --user status "$SERVICE_NAME.service" --no-pager || true

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  docs2md autostart service installed!                         ║"
echo "║                                                               ║"
echo "║  Service will start automatically on login.                   ║"
echo "║  API:      http://127.0.0.1:${PORT}                                 ║"
echo "║  API docs: http://127.0.0.1:${PORT}/docs                            ║"
echo "║                                                               ║"
echo "║  Manage the service:                                          ║"
echo "║    systemctl --user start docs2md                             ║"
echo "║    systemctl --user stop docs2md                              ║"
echo "║    systemctl --user status docs2md                            ║"
echo "║    systemctl --user disable docs2md   # remove autostart      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
