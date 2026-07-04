#!/bin/bash
# Supervised backend (ASGI: HTTP + websockets). Restarted by launchd KeepAlive.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/orc-stack.env"
export PATH="$ORC_PATH_EXTRA:$PATH"
cd "$ORC_REPO/backend"
exec "$ORC_REPO/backend/.venv/bin/daphne" -b "${ORC_BIND_HOST:-127.0.0.1}" -p 8000 config.asgi:application
