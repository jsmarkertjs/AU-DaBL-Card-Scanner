#!/usr/bin/env bash
# Install the AU Card Reader from a git clone.
#
#   sudo apt install -y git
#   git clone <repo-url> ~/au-card-reader
#   sudo bash ~/au-card-reader/deploy/install.sh
#
# Re-running this script (e.g. after `git pull`) re-installs the service.
# Existing config/token in /etc/au-card-reader are preserved.
set -euo pipefail

# Repo root = parent of the directory containing this script.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR=/etc/au-card-reader
SERVICE_USER="${SUDO_USER:-pi}"

echo "Installing system packages..."
apt-get update
apt-get install -y \
    git \
    python3-requests \
    python3-yaml \
    python3-evdev \
    python3-gpiozero

echo "Using application at ${REPO_DIR}..."

echo "Setting up configuration in ${CONF_DIR}..."
mkdir -p "${CONF_DIR}"
if [ ! -f "${CONF_DIR}/config.yaml" ]; then
    cp "${REPO_DIR}/config.example.yaml" "${CONF_DIR}/config.yaml"
    echo "  -> edit ${CONF_DIR}/config.yaml with your course/assignment ids"
fi
if [ ! -f "${CONF_DIR}/env" ]; then
    cp "${REPO_DIR}/deploy/env.example" "${CONF_DIR}/env"
    chmod 600 "${CONF_DIR}/env"
    echo "  -> put your Canvas token in ${CONF_DIR}/env"
fi
if [ -f "${REPO_DIR}/auid_to_canvas_id.csv" ] \
    && [ ! -f "${CONF_DIR}/auid_to_canvas_id.csv" ]; then
    cp "${REPO_DIR}/auid_to_canvas_id.csv" "${CONF_DIR}/auid_to_canvas_id.csv"
fi

echo "Adding ${SERVICE_USER} to the input group..."
usermod -aG input "${SERVICE_USER}"

echo "Installing systemd service..."
sed -e "s|^User=.*|User=${SERVICE_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_DIR}|" \
    "${REPO_DIR}/deploy/au-card-reader.service" \
    > /etc/systemd/system/au-card-reader.service
systemctl daemon-reload
systemctl enable au-card-reader.service
systemctl restart au-card-reader.service

echo
echo "Done. Check status with:  systemctl status au-card-reader"
echo "Follow logs with:         journalctl -u au-card-reader -f"
echo "List input devices with:  python3 -m au_card_reader --list-devices"
