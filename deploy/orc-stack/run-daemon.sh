#!/bin/bash
# Supervised host-agent daemon: ws recv loop (headless.prompt / pty.inject) +
# optional observation. ORC_HEADLESS_ENGINE (from env) selects the driving path.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/orc-stack.env"
export PATH="$ORC_PATH_EXTRA:$PATH"
cd "$ORC_REPO/host-agent"
# runtimes=[] unless OBSERVE_RUNTIMES is set (avoids read-only mirror clutter);
# the recv loop (headless/inject) runs regardless of runtimes.
exec "$ORC_REPO/host-agent/.venv/bin/python" -c "
import os
from agent_host.config import load
from agent_host.daemon import run
cfg = load()
assert cfg is not None, 'host not enrolled (run orc-host enroll)'
rt = [r for r in os.environ.get('OBSERVE_RUNTIMES','').split(',') if r]
run(cfg, runtimes=rt)
"
