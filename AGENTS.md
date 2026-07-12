# AGENTS.md

## Project overview

OpenRemote-Control is a self-hosted control plane for AI coding agents (Codex, Codex, Gemini, and any MCP-capable tool). It dispatches each coding-agent session to a chat topic in an app you already supervise (Telegram first, or WhatsApp/Slack/Discord/Signal/iMessage via a Node sidecar). The backend is Django + DRF + Channels (ASGI) backed by PostgreSQL and Valkey; a per-machine Python host-agent daemon enrolls each host and streams PTY turns; an installable MCP bridge (`orc-mcp`) gives agents four dispatch calls routed through the sovereign backend. A persistent `Codex -p` stream-json engine drives headless sessions turn-by-turn, while a scoped editor-turn mirror surfaces typed-in-editor turns without double-posting chat-driven ones.

## Repository layout

- `backend/` — Django + DRF + Channels control plane (ASGI). Apps under `apps/` (accounts, hosts, projects, threads, prompts, approvals, observe, hostlink, connectors, gateway, telegram, slash, supervisor, policies, audit, tier2, skills). `config/` holds settings, ASGI/WSGI, Channels routing, Celery.
- `host-agent/` — Python 3.12+ daemon per machine: enrollment, PTY streaming, input-safety policy, headless engines (`interactive_engine`, `sdk_session`, `claude_headless`), transcript tail.
- `connectors/orc-mcp/` — installable MCP bridge client exposing `openremote_control` / `notify` / `ask_human` / `request_approval`.
- `connectors/messaging-gateway/` — Node/TypeScript sidecar bridging WhatsApp, Slack, Discord, Signal, iMessage to the backend.
- `deploy/` — Docker Compose (backend app + headscale), Caddy reverse proxy, and `orc-stack/` launchd supervisor runbooks + plists (macOS).

## Test commands

All commands run from the repo root unless prefixed with a `cd`.

- **backend:** `cd backend && POSTGRES_PORT=5544 .venv/bin/python -m pytest` — Postgres must be up via `docker compose` on host port 5544. NEVER run bare `pytest`; it connects to whatever squats on port 5432 and ~190 tests fail with auth errors.
- **host-agent:** `cd host-agent && .venv/bin/python -m pytest`
- **orc-mcp:** `cd connectors/orc-mcp && .venv/bin/python -m pytest tests`
- **messaging-gateway:** `cd connectors/messaging-gateway && npm test`

NEVER run the backend test suite with `config.settings.local` selected — it reads a real Telegram token from the environment and will hit live Telegram.

CI (`.github/workflows/ci.yml`) runs the same suites per job: backend also lints with `ruff`, runs `bandit` and advisory `mypy`; host-agent lints with `ruff` + `bandit`; orc-mcp and gateway run their test suites. Match CI locally before pushing.

## Key conventions

- **ASGI only.** Daphne serves the app (HTTP + WebSockets). Never use `runserver` — it cannot serve Channels WebSockets.
- **Headless engine selection.** `ORC_HEADLESS_ENGINE` selects the daemon's drive engine: `interactive` (one long-lived `Codex -p` stream-json process per session), `sdk` (Agent SDK path), or unset (CLI prompt path). The deploy `run-daemon.sh` wrapper reads this.
- **Transcript tail is single-file.** The transcript tail must NEVER scan directories — it tails one transcript path only. Directory scans are an O(N) footgun that was removed; do not reintroduce them.
- **Frame shape.** Frames between the host-agent daemon and the backend WebSocket consumer are data-wrapped: `{type: "<name>", data: {...}}`. Keep that envelope; consumers dispatch on `type` and read fields from `data`.
- **Telegram allowlist is default-deny.** Only allowlisted chat/topic IDs receive responses; everything else is dropped. Do not widen this without a policy change.
- **Migrations.** `makemigrations` is run by the developer; migrations are generated and committed but applied by the operator (deploy runbook), not by the test suite or by `runserver`.

## Boundary rules

- **Do not touch `backend/apps/hostlink/security.py` or `host-agent/agent_host/input_policy.py` without a security review.** The former is the HMAC host-enrollment/signing core, the latter the PTY input-safety policy; open an issue and get design review before editing either.
- **Never commit `.env`, `orc-stack.env`, or any `*.key`/`*.pem`/`*.p12`/`*.pfx`.** `deploy/orc-stack/orc-stack.env` is gitignored; only the `.env.example` is tracked. Check staged files for these patterns before every commit.
- **Never run two `run_telegram_bot` processes.** The Telegram `getUpdates` consumer is single-instance by contract; a second process polls and steals updates. The launchd `run-bot.sh` supervisor enforces exactly-one; respect it.
- **No AI attribution in git artifacts.** No `Co-Authored-By`, `Authored-By`, or AI/Codex/Anthropic mentions in commits, PRs, branches, or comments.