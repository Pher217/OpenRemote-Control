#!/bin/bash
# Supervised host-agent daemon: ws recv loop only (headless.prompt / pty.inject /
# session.start). ORC_HEADLESS_ENGINE (from env) selects the driving path. There
# is no read-only observation — the daemon only drives sessions, never mirrors.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/orc-stack.env"
export PATH="$ORC_PATH_EXTRA:$PATH"
cd "$ORC_REPO/host-agent"
# -u: unbuffered stdout/stderr so launchd-redirected logs flush line-by-line.
# Without it, Python block-buffers under a non-tty fd and the daemon log looks
# frozen for minutes (a healthy daemon is quiet, so the buffer rarely fills).
exec "$ORC_REPO/host-agent/.venv/bin/python" -u -c "
from agent_host.config import load
from agent_host.daemon import run
cfg = load()
assert cfg is not None, 'host not enrolled (run orc-host enroll)'
run(cfg)
"
