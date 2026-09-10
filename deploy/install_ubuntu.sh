#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/chief-signal-dashboard
REPO=https://github.com/Jhanser21/chief-signal-dashboard.git

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git ca-certificates curl

if ! id chief >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /bin/bash chief
fi

sudo mkdir -p "$APP_DIR"
sudo chown -R chief:chief "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u chief git clone "$REPO" "$APP_DIR"
else
  sudo -u chief git -C "$APP_DIR" pull --ff-only
fi

sudo -u chief python3 -m venv "$APP_DIR/.venv"
sudo -u chief "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u chief "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u chief cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env — add Telegram credentials before starting the bot."
fi

sudo cp "$APP_DIR/deploy/chief-bot.service" /etc/systemd/system/chief-bot.service
sudo systemctl daemon-reload
sudo systemctl enable chief-bot.service

echo "Chief Bot code installed."
echo "Next: install/login to Moomoo OpenD on this server, edit $APP_DIR/.env, then run:"
echo "  sudo systemctl start chief-bot"
echo "  sudo journalctl -u chief-bot -f"
