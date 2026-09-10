#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/chief-signal-dashboard"
BRANCH="main"
LOCK_FILE="/run/chief-auto-update.lock"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$REPO_DIR"

git fetch origin "$BRANCH" --quiet
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/$BRANCH)"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
    exit 0
fi

OLD_SHA="$LOCAL_SHA"
echo "Chief auto-update: deploying $OLD_SHA -> $REMOTE_SHA"

git pull --ff-only origin "$BRANCH"

# Keep the virtual environment synced whenever dependencies change.
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt" --quiet

# Validate Python before restarting the live bot.
if ! "$REPO_DIR/.venv/bin/python" -m py_compile "$REPO_DIR/chief_bot.py" "$REPO_DIR/chief_patterns.py"; then
    echo "Chief auto-update: validation failed, rolling back to $OLD_SHA"
    git reset --hard "$OLD_SHA"
    exit 1
fi

systemctl restart chief-bot
sleep 2

if systemctl is-active --quiet chief-bot; then
    echo "Chief auto-update: deployment successful at $REMOTE_SHA"
else
    echo "Chief auto-update: chief-bot failed after deployment; rolling back to $OLD_SHA"
    git reset --hard "$OLD_SHA"
    systemctl restart chief-bot
    exit 1
fi
