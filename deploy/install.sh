#!/usr/bin/env bash
# Install the AU card reader on a Raspberry Pi from a git clone.
#
#   sudo apt install -y git
#   git clone https://github.com/jsmarkertjs/AU-DaBL-Card-Scanner.git ~/au-card-reader
#   sudo bash ~/au-card-reader/deploy/install.sh
#
# Re-running after "git pull" reinstalls the service; /etc/au-card-reader is kept.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR=/etc/au-card-reader
SERVICE_USER="${SUDO_USER:-pi}"

echo "Installing system packages..."
apt-get update
apt-get install -y python3-requests python3-serial python3-evdev

echo "Setting up configuration in ${CONF_DIR}..."
mkdir -p "${CONF_DIR}"
if [ ! -f "${CONF_DIR}/config.ini" ]; then
    cp "${REPO_DIR}/config.ini.example" "${CONF_DIR}/config.ini"
    echo "  -> edit ${CONF_DIR}/config.ini with course_id / assignment_id"
fi
if [ ! -f "${CONF_DIR}/env" ]; then
    cp "${REPO_DIR}/deploy/env.example" "${CONF_DIR}/env"
    chmod 600 "${CONF_DIR}/env"
    echo "  -> put your Canvas token in ${CONF_DIR}/env"
fi

echo "Adding ${SERVICE_USER} to input + dialout groups..."
usermod -aG input,dialout "${SERVICE_USER}" || true

echo "Installing systemd service..."
sed -e "s|^User=.*|User=${SERVICE_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_DIR}|" \
    "${REPO_DIR}/deploy/au-card-reader.service" \
    > /etc/systemd/system/au-card-reader.service
systemctl daemon-reload
systemctl enable au-card-reader.service
systemctl restart au-card-reader.service

echo
echo "Done. Edit ${CONF_DIR}/config.ini and ${CONF_DIR}/env, then:"
echo "  sudo systemctl restart au-card-reader"
echo "Logs: journalctl -u au-card-reader -f"
