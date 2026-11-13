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

echo "Installing system packages..."
apt-get update
apt-get install -y python3-requests python3-serial python3-evdev mpg123 alsa-utils \
    python3-pil python3-pip python3-dev fonts-dejavu

echo "Enabling SPI (for the eInk display)..."
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_spi 0 || true
fi

# Free CE0 (GPIO8) from the SPI driver so it can be used as the eInk
# chip-select. Without this, GPIO8 is claimed and the display fails to init.
CONFIG_TXT=/boot/firmware/config.txt
[ -f "${CONFIG_TXT}" ] || CONFIG_TXT=/boot/config.txt
if [ -f "${CONFIG_TXT}" ] && ! grep -q "dtoverlay=spi0-0cs" "${CONFIG_TXT}"; then
    echo "dtoverlay=spi0-0cs" >> "${CONFIG_TXT}"
    echo "  -> added dtoverlay=spi0-0cs to ${CONFIG_TXT} (REBOOT required)"
fi

echo "Installing eInk + NeoPixel libraries (Blinka, EPD, NeoPixel)..."
PIP_BREAK=""
if pip3 install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
    PIP_BREAK="--break-system-packages"
fi
pip3 install ${PIP_BREAK} adafruit-blinka adafruit-circuitpython-epd \
    adafruit-circuitpython-framebuf adafruit-circuitpython-neopixel rpi_ws281x \
    || echo "  (pip install failed; the display / NeoPixel tower will stay disabled)"

# Blinka requires gpiod >= 2.2.2, but Raspbian trixie ships 2.2.0. Install a
# shadow copy (--ignore-installed, since apt owns the old one) so board imports.
pip3 install ${PIP_BREAK} --ignore-installed "gpiod>=2.2.2,<2.3" \
    || echo "  (gpiod install failed; Blinka may refuse to load)"

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

echo "Installing systemd service (runs as root for the NeoPixel/PWM)..."
sed -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_DIR}|" \
    "${REPO_DIR}/deploy/au-card-reader.service" \
    > /etc/systemd/system/au-card-reader.service
systemctl daemon-reload
systemctl enable au-card-reader.service
systemctl restart au-card-reader.service

echo
echo "Done. Edit ${CONF_DIR}/config.ini and ${CONF_DIR}/env, then:"
echo "  sudo systemctl restart au-card-reader"
echo "Logs: journalctl -u au-card-reader -f"
