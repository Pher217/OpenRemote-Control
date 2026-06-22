# orc-stack — launchd supervisor (macOS)

Keeps the OpenRemote-Control reverse-input stack alive so it is **not "broken
every morning"**. The three processes are hand-startable but had no supervisor;
launchd `KeepAlive` restarts them on crash and `RunAtLoad` starts them at login.

## Processes
| Service | What | Wrapper |
|---|---|---|
| `com.openremote.daphne` | backend ASGI (HTTP + ws) on :8000 | `run-daphne.sh` |
| `com.openremote.bot` | inbound Telegram bot (single getUpdates consumer) | `run-bot.sh` |
| `com.openremote.daemon` | host-agent daemon (headless.prompt / pty.inject recv loop) | `run-daemon.sh` |

All config (paths, `ORC_HEADLESS_ENGINE`, `OBSERVE_DELIVERY_MODE`, `OBSERVE_RUNTIMES`)
lives in `orc-stack.env` — the single source of truth.

## Use
```bash
deploy/orc-stack/install.sh     # install + start (idempotent; kills hand-started dupes first)
deploy/orc-stack/status.sh      # state + pids + backend probe + one-bot check
deploy/orc-stack/uninstall.sh   # stop + remove
# after editing orc-stack.env or pulling code:
launchctl kickstart -k gui/$(id -u)/com.openremote.daemon   # restart one service
```
Logs: `~/Library/Logs/orc-stack/{daphne,bot,daemon}.log`.

## Rollback the driving engine
`ORC_HEADLESS_ENGINE=sdk` (default) → Agent-SDK path with Allow/Deny tool buttons.
Unset it in `orc-stack.env` → legacy `claude -p` path. Kickstart the daemon after.

## Not covered (follow-ups)
- Windows supervisor (Task Scheduler / NSSM) — see the cross-platform spec.
- Postgres/redis are assumed already running (Homebrew services).
