#!/bin/bash
# Show orc-stack supervisor status + a backend health probe.
DOM="gui/$(id -u)"
for svc in com.openremote.daphne com.openremote.bot com.openremote.daemon; do
  line=$(launchctl print "$DOM/$svc" 2>/dev/null | grep -E "state =|pid =" | tr -d ' ' | paste -sd' ' -)
  echo "$svc: ${line:-not loaded}"
done
code=$(curl -s --max-time 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/connectors/result/x 2>/dev/null || echo 000)
echo "backend :8000 -> HTTP ${code} (401/503 = up)"
echo "one-bot check: $(pgrep -f 'manage.py run_telegram_bot' | wc -l | tr -d ' ') bot process(es) (must be 1)"
