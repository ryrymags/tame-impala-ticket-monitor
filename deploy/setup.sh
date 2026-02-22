#!/bin/bash
# Oracle VM setup script — run once after cloning the repo.
# Usage: bash deploy/setup.sh
set -e

echo "=== Tame Impala Ticket Monitor — VM Setup ==="

# Install Python if missing
if ! command -v python3 &>/dev/null; then
    echo "Installing Python 3..."
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv
fi

# Create venv and install deps
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt
echo "Dependencies installed."

# Prompt for config if still using placeholders
if grep -q "YOUR_API_KEY_HERE" config.yaml 2>/dev/null; then
    echo ""
    echo ">>> config.yaml still has placeholder values."
    echo ">>> Edit it now:  nano config.yaml"
    echo ">>> Then re-run this script."
    exit 1
fi

# Install systemd service
echo "Installing systemd service..."
REPO_DIR="$(pwd)"
SERVICE_USER="$(whoami)"

sudo tee /etc/systemd/system/ticket-monitor.service > /dev/null <<EOF
[Unit]
Description=Tame Impala Ticket Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/venv/bin/python monitor.py
Restart=always
RestartSec=30

# Keep logs tidy — stdout/stderr go to journald
StandardOutput=journal
StandardError=journal

# Memory guard for free-tier VM (1 GB RAM)
MemoryMax=256M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ticket-monitor
sudo systemctl start ticket-monitor

echo ""
echo "=== Setup complete ==="
echo "Monitor is running. Useful commands:"
echo "  sudo systemctl status ticket-monitor   # Check status"
echo "  sudo journalctl -u ticket-monitor -f   # Follow logs"
echo "  sudo systemctl restart ticket-monitor   # Restart"
echo "  python monitor.py --heartbeat           # Manual heartbeat"
echo "  python monitor.py --recap               # Manual recap"
