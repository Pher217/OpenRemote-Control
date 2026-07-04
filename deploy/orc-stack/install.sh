#!/bin/bash
# Install the orc-stack launchd supervisor (macOS). Idempotent.
# Makes daphne + bot + host-daemon self-heal (KeepAlive) and start at login,
# so the Telegram reverse-input stack is no longer "broken every morning".
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LA="$HOME/Library/LaunchAgents"
DOM="gui/$(id -u)"
SVCS=(com.openremote.daphne com.openremote.bot com.openremote.daemon)

# shellcheck disable=SC1091
source "$DIR/orc-stack.env"
ORC_LOGS="${ORC_LOGS:-$HOME/Library/Logs/orc-stack}"

mkdir -p "$ORC_LOGS" "$LA"
chmod +x "$DIR"/run-*.sh

# Kill any hand-started instances first (avoid duplicate getUpdates consumers).
pkill -9 -f "daphne -b 127.0.0.1 -p 8000" 2>/dev/null || true
pkill -9 -f "manage.py run_telegram_bot" 2>/dev/null || true
pkill -9 -f "agent_host.config import load" 2>/dev/null || true

for svc in "${SVCS[@]}"; do
  # Render the tracked .plist.template (portable — no personal paths) into a
  # real plist under LaunchAgents by substituting ORC_REPO / ORC_LOGS.
  sed -e "s|@ORC_REPO@|$ORC_REPO|g" -e "s|@ORC_LOGS@|$ORC_LOGS|g" \
    "$DIR/$svc.plist.template" > "$LA/$svc.plist"
  launchctl bootout "$DOM/$svc" 2>/dev/null || true
  launchctl bootstrap "$DOM" "$LA/$svc.plist"
  launchctl enable "$DOM/$svc"
  echo "installed + started: $svc"
done
echo "Done. Status: $DIR/status.sh   Logs: $ORC_LOGS/"
