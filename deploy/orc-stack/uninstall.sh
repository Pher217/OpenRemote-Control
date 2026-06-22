#!/bin/bash
# Remove the orc-stack launchd supervisor.
set -euo pipefail
LA="$HOME/Library/LaunchAgents"
DOM="gui/$(id -u)"
for svc in com.openremote.daphne com.openremote.bot com.openremote.daemon; do
  launchctl bootout "$DOM/$svc" 2>/dev/null || true
  rm -f "$LA/$svc.plist"
  echo "removed: $svc"
done
