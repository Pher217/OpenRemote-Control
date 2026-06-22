#!/bin/bash
# Supervised inbound Telegram bot (single getUpdates consumer). Restarted by launchd.
# CRITICAL: exactly one instance — two consumers steal each other's updates.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/orc-stack.env"
export PATH="$ORC_PATH_EXTRA:$PATH"
cd "$ORC_REPO/backend"
exec "$ORC_REPO/backend/.venv/bin/python" manage.py run_telegram_bot
