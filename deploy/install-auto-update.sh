#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/chief-signal-dashboard"

chmod +x "$REPO_DIR/deploy/chief-auto-update.sh"
cp "$REPO_DIR/deploy/chief-auto-update.service" /etc/systemd/system/chief-auto-update.service
cp "$REPO_DIR/deploy/chief-auto-update.timer" /etc/systemd/system/chief-auto-update.timer

systemctl daemon-reload
systemctl enable --now chief-auto-update.timer

echo "Chief automatic deployment is installed."
systemctl status chief-auto-update.timer --no-pager
