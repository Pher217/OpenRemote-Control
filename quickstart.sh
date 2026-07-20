#!/usr/bin/env bash
#
# quickstart.sh — one-command setup for a local, single-machine OpenRemote Control.
#
# Brings up the whole backend stack in Docker (backend + Postgres + Valkey +
# Telegram bot), generates all secrets, validates your bot token, auto-discovers
# your Telegram chat, and enrolls this machine's host daemon. After it finishes,
# run /openremote-control inside Claude Code.
#
# It cannot do three things for you (each takes ~30s):
#   1. Install Docker.
#   2. Create a bot with @BotFather and copy its token.
#   3. Create a Telegram group with Topics enabled and add your bot as admin.
#
# Re-running is safe: it never overwrites an existing deploy/.env without asking.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_ROOT/deploy/.env"
ENV_EXAMPLE="$REPO_ROOT/deploy/.env.example"
COMPOSE_FILE="$REPO_ROOT/deploy/app/docker-compose.yml"
BACKEND_URL="http://localhost:8000"

c_bold=$'\033[1m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_reset=$'\033[0m'
say()  { printf '%s\n' "${c_bold}▸ $*${c_reset}"; }
ok()   { printf '%s\n' "${c_green}✓ $*${c_reset}"; }
warn() { printf '%s\n' "${c_yellow}! $*${c_reset}"; }
die()  { printf '%s\n' "${c_red}✗ $*${c_reset}" >&2; exit 1; }

# --- 0. preflight ---------------------------------------------------------
say "Checking prerequisites"
command -v docker  >/dev/null 2>&1 || die "Docker is not installed — see https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) is required — update Docker Desktop / the compose plugin."
command -v python3 >/dev/null 2>&1 || die "python3 is required (used for token validation and secret generation)."
command -v curl    >/dev/null 2>&1 || die "curl is required."
docker info >/dev/null 2>&1 || die "Docker daemon is not running — start Docker and re-run."
ok "docker, compose, python3, curl present"

# --- helpers --------------------------------------------------------------
gen_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }

# Read a value from a Telegram getMe/getUpdates JSON via python (no jq dependency).
tg_json() { # $1=token $2=method  → prints raw JSON
  curl -sS --max-time 15 "https://api.telegram.org/bot$1/$2"
}

# --- 1. .env --------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  warn "deploy/.env already exists."
  read -r -p "Reuse it (r) or start fresh, overwriting (o)? [r/o] " choice
  case "$choice" in
    o|O) : ;;                                  # fall through and regenerate
    *)   REUSE_ENV=1 ;;
  esac
fi

if [ -z "${REUSE_ENV:-}" ]; then
  [ -f "$ENV_EXAMPLE" ] || die "deploy/.env.example not found — are you running this from the repo root?"
  say "Generating deploy/.env with fresh secrets"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  # This file is about to hold the bot token and every control-plane secret.
  # .env.example ships 0644; narrow it before anything is written into it so
  # the secrets are never briefly world-readable on a multi-user host.
  chmod 600 "$ENV_FILE"
  # Fill the auto-generated secrets (portable in-place sed via a temp file).
  set_kv() { # $1=key $2=value
    python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys, re
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines()
out, seen = [], False
for ln in lines:
    if re.match(rf'^{re.escape(key)}=', ln):
        out.append(f'{key}={val}'); seen = True
    else:
        out.append(ln)
if not seen:
    out.append(f'{key}={val}')
open(path, 'w').write('\n'.join(out) + '\n')
PY
  }
  set_kv SECRET_KEY "$(gen_secret)"
  set_kv POSTGRES_PASSWORD "$(gen_secret)"
  set_kv ORC_ENROLL_SECRET "$(gen_secret)"
  set_kv ORC_CONNECTOR_TOKEN "$(gen_secret)"
  set_kv MESSAGING_GATEWAY_TOKEN "$(gen_secret)"
  ok "Secrets generated (SECRET_KEY, POSTGRES_PASSWORD, enroll/connector/gateway tokens)"

  # --- 2. bot token ------------------------------------------------------
  say "Telegram bot token"
  echo "  Create a bot with @BotFather (https://t.me/BotFather) → /newbot → copy the token."
  while :; do
    read -r -p "  Paste your bot token: " BOT_TOKEN
    BOT_TOKEN="$(printf '%s' "$BOT_TOKEN" | tr -d '[:space:]')"
    [ -n "$BOT_TOKEN" ] || { warn "empty — try again"; continue; }
    resp="$(tg_json "$BOT_TOKEN" getMe || true)"
    if printf '%s' "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null; then
      uname="$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])')"
      ok "Validated bot @$uname"
      break
    fi
    warn "Telegram rejected that token — check for extra characters and try again."
  done
  set_kv TELEGRAM_BOT_TOKEN "$BOT_TOKEN"

  # --- 3. discover chat + user id ---------------------------------------
  say "Telegram group discovery"
  echo "  1) Create a group, open its settings, and turn ON 'Topics'."
  echo "  2) Add @$uname to the group as an admin."
  echo "  3) Send any message in the group (e.g. 'hi')."
  read -r -p "  Done? Press Enter to detect the group… " _
  FORUM_ID=""; USER_ID=""
  for attempt in 1 2 3 4 5; do
    updates="$(tg_json "$BOT_TOKEN" getUpdates || true)"
    read -r FORUM_ID USER_ID <<EOF2
$(printf '%s' "$updates" | python3 -c '
import sys, json
d = json.load(sys.stdin)
chat_id = user_id = ""
for u in d.get("result", []):
    msg = u.get("message") or u.get("channel_post") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") in ("supergroup", "group"):
        chat_id = str(chat.get("id"))
        frm = msg.get("from") or {}
        if frm.get("id"): user_id = str(frm["id"])
print(chat_id, user_id)
' 2>/dev/null)
EOF2
    [ -n "$FORUM_ID" ] && break
    warn "No group message seen yet (attempt $attempt/5) — send a message in the group…"
    sleep 3
  done
  if [ -z "$FORUM_ID" ]; then
    warn "Could not auto-detect the group. You can fill these in deploy/.env later:"
    warn "  TELEGRAM_FORUM_CHAT_ID, ORC_PROMPT_CHAT_ID (your group id), TELEGRAM_ALLOWED_CHAT_IDS (your user id)."
  else
    set_kv TELEGRAM_FORUM_CHAT_ID "$FORUM_ID"
    set_kv ORC_PROMPT_CHAT_ID "$FORUM_ID"
    [ -n "$USER_ID" ] && set_kv TELEGRAM_ALLOWED_CHAT_IDS "$USER_ID"
    ok "Group $FORUM_ID detected${USER_ID:+, you are user $USER_ID (allowlisted)}"
  fi
fi

# --- 4. bring up the stack ------------------------------------------------
say "Starting the stack (docker compose up -d)"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
say "Waiting for the backend to become healthy…"
for i in $(seq 1 60); do
  if curl -sf "$BACKEND_URL/health/" >/dev/null 2>&1; then ok "Backend healthy at $BACKEND_URL"; break; fi
  [ "$i" = 60 ] && die "Backend did not become healthy in 60s — check: docker compose -f $COMPOSE_FILE logs web"
  sleep 1
done

# --- 5. enroll the local host daemon --------------------------------------
say "Enrolling this machine's host daemon"
ENROLL_SECRET="$(python3 -c 'import sys,re; [print(l.split("=",1)[1]) for l in open(sys.argv[1]) if l.startswith("ORC_ENROLL_SECRET=")]' "$ENV_FILE" | head -1)"
HOST_BIN=""
if [ -x "$REPO_ROOT/host-agent/.venv/bin/orc-host" ]; then
  HOST_BIN="$REPO_ROOT/host-agent/.venv/bin/orc-host"
elif command -v orc-host >/dev/null 2>&1; then
  HOST_BIN="$(command -v orc-host)"
fi

# On a fresh clone host-agent is not installed yet. Install it here rather than
# printing instructions and declaring success — the daemon is what actually
# drives sessions, so without it /openremote-control cannot work.
if [ -z "$HOST_BIN" ]; then
  if command -v uv >/dev/null 2>&1; then
    say "Installing host-agent (uv sync)"
    if (cd "$REPO_ROOT/host-agent" && uv sync >/dev/null 2>&1); then
      [ -x "$REPO_ROOT/host-agent/.venv/bin/orc-host" ] \
        && HOST_BIN="$REPO_ROOT/host-agent/.venv/bin/orc-host"
    else
      warn "uv sync failed in host-agent/."
    fi
  else
    warn "uv is not installed — cannot install the host-agent automatically."
    echo "    Install uv:  https://docs.astral.sh/uv/getting-started/installation/"
  fi
fi

HOST_READY=0
if [ -n "$HOST_BIN" ] && [ -n "$ENROLL_SECRET" ]; then
  if "$HOST_BIN" enroll --backend "$BACKEND_URL" --secret "$ENROLL_SECRET"; then
    ok "Host enrolled"
    HOST_READY=1
  else
    warn "Enrollment failed."
  fi
elif [ -z "$ENROLL_SECRET" ]; then
  warn "ORC_ENROLL_SECRET missing from $ENV_FILE — cannot enroll."
fi

# --- done -----------------------------------------------------------------
echo
if [ "$HOST_READY" -eq 1 ]; then
  ok "Setup complete."
  echo
  say "Start the host daemon (keep this running while you drive sessions):"
  echo "  ORC_HEADLESS_ENGINE=interactive $HOST_BIN daemon"
  echo
  echo "  Then: open Claude Code and run  ${c_bold}/openremote-control${c_reset}"
  echo "  Reply in your Telegram group to drive the session from your phone."
else
  warn "Setup INCOMPLETE — the backend is up, but this machine is not enrolled."
  echo "  /openremote-control will not work until the host daemon is enrolled."
  echo
  echo "  Finish with:"
  echo "    cd host-agent && uv sync && \\"
  echo "      .venv/bin/orc-host enroll --backend $BACKEND_URL --secret \"\$ORC_ENROLL_SECRET\""
  echo "    ORC_HEADLESS_ENGINE=interactive .venv/bin/orc-host daemon"
  echo
  echo "  ORC_ENROLL_SECRET is in $ENV_FILE."
fi
echo
echo "  Stop the stack:  docker compose -f $COMPOSE_FILE down"
