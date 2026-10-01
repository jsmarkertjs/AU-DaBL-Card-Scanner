#!/usr/bin/env bash
# Update the AU Card Reader to the latest code and restart the service.
#
#   cd ~/au-card-reader && sudo bash deploy/update.sh
#
# (Or from anywhere: sudo bash ~/au-card-reader/deploy/update.sh)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Pulling latest code in ${REPO_DIR}..."
git -C "${REPO_DIR}" pull --ff-only

systemctl restart au-card-reader.service
echo "Updated and restarted. Follow logs: journalctl -u au-card-reader -f"
