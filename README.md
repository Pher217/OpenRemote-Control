# OpenRemote Control

> A self-hosted control plane for the AI coding agents you already run — Claude Code, Codex, Gemini, and any MCP tool like Cursor or Copilot, across all your machines — routed into one Telegram/Matrix inbox you supervise from your phone.

AI agents now run everywhere: laptops, VPSes, workstations, terminals, editors, MCP tools. The hard part isn't starting them — it's noticing when one needs you. An agent can sit paused on a yes/no in a tmux pane you closed hours ago, while you're nowhere near that keyboard.

OpenRemote Control turns every agent session into a chat in the app you already use. Agents **notify** you, **ask** you questions, and **request approval** — and you answer once, from Telegram or Matrix, without hunting down the right machine, terminal, or editor.

It's sovereign by design: you host it, your sessions and credentials never leave your infrastructure, and it reaches your tools through official SDKs and the open Model Context Protocol — never browser scraping or vendor hacks.

## How it works

```
   Agents & machines                OpenRemote Control          Your app of choice
 ──────────────────────────       ─────────────────────       ─────────────────────
 Claude Code / Codex / Gemini ─▶                               Telegram
 host-agent (each machine)    ─▶   one chat session per   ◀─▶  Matrix
 MCP tools + orc-mcp          ─▶   agent · policy ·            → WhatsApp / Slack /
   notify · ask · approve          approvals · audit              Signal
```

You live in **one app you already use** — Telegram, or WhatsApp / Slack / Signal through Matrix bridges. Every agent session shows up there as an ordinary chat you read and reply to; there is no separate inbox to learn. Start one yourself, right from that chat, with the universal command:

```
/openremote-control [name]      # short alias: /orc
```

It opens a fresh chat session (named, if you pass one) in the channel you're already in, and the conversation simply continues in your app of choice.

Agents reach that same chat two ways:

1. **Observe** — a read-only watcher tails your existing agent sessions (Claude Code, Codex, Gemini) and surfaces each as a chat. Nothing is hijacked; the watcher reads, it doesn't drive.
2. **The universal MCP bridge (`orc-mcp`)** — a small MCP server you install into *any* MCP-capable tool (Cursor, Copilot, Codex, Claude Code, Kiro, …). It is purely a **dispatch tool**: the agent gains three calls that route through *your* backend into the same chat —
   - `notify(message)` — push progress to your chat
   - `ask_human(question, options)` — ask you something and wait for the reply
   - `request_approval(action, preview)` — request permission for an action (**fail-closed**: denies on timeout)

   This is opt-in and agent-initiated — the agent calls out to you. No vendor API is impersonated, no closed UI is driven.

## Status — backend foundation in place

The backend is implemented and tested: PRs [#1](https://github.com/Pher217/OpenRemote-Control/pull/1)–[#6](https://github.com/Pher217/OpenRemote-Control/pull/6) merged, **~438 tests passing**, live ASGI smoke test green.

**Shipped:**

- Multi-runtime **observe** (Claude Code / Codex / Gemini) via a pluggable runtime registry
- Interactive **answer-in-chat** (the `Prompt` primitive)
- The universal **`/openremote-control`** (alias `/orc`) command — start a named chat session from any surface, then follow it in your app of choice
- The **universal MCP bridge** — `apps.connectors` backend + the installable [`orc-mcp`](connectors/orc-mcp/README.md) client
- **Telegram + Matrix** surfaces (→ WhatsApp / Slack / Signal via mautrix bridges)
- **Multi-host** backend (`apps.hostlink`) + a **host daemon client** (`host-agent`: `orc-host enroll | daemon`)
- A **PTY input-safety core** that the next milestone builds on

**In progress / next:**

- A **deploy runbook** (docker-compose: Matrix homeserver + bridges + headscale; daemon on a second machine)
- `orc run` — approval-gated remote terminal (PTY) streaming, building on the existing input-safety core
- Per-connector keypair identity hardening (replacing the shared bearer token) before any multi-user use
- The Next.js PWA frontend (scaffolded; not yet built)

## Why it's different

- **Sovereign / self-hosted** — no SaaS, no hosted middleman. Sessions, prompts, approvals, and credentials stay on hardware you own. The whole stack is OSS and self-hostable.
- **Multi-host** — your MacBook, Windows workstation, and Linux VPS become one fleet, one inbox. A host daemon enrolls each machine over your private network.
- **Multi-runtime** — Claude Code, Codex, and Gemini today via the runtime registry; any MCP tool via the bridge.
- **Two-way, not just a viewer** — agents ask, you answer; agents request, you approve — right from chat.
- **Policy + approval + audit built in** — sensitivity-aware project profiles, risk-tiered approvals, and an append-only Postgres audit log across heterogeneous runtimes.
- **Surfaces you already use** — Telegram and Matrix today; Matrix bridges fan out to WhatsApp, Slack, Signal, and more.

## What it is not

- a new AI agent
- a replacement for Claude Code, Codex, Gemini, Cursor, or Copilot
- a hosted SaaS, a workflow builder, or an IDE
- a browser-scraping wrapper around closed vendor UIs

If a tool can't be reached through an SDK, exported session files, or MCP, it stays out of scope — no brittle browser hacks.

## Try the bridge

The fastest way to see this work: connect one tool and have its agent send a notification or ask a question through your chat inbox. With a backend running, install [`orc-mcp`](connectors/orc-mcp/README.md) into any MCP tool:

```bash
# Claude Code
claude mcp add orc -- orc-mcp
```

```toml
# OpenAI Codex CLI — ~/.codex/config.toml
[mcp_servers.orc]
command = "orc-mcp"
```

```json
// Cursor — ~/.cursor/mcp.json
{ "mcpServers": { "orc": { "command": "orc-mcp" } } }
```

Set `ORC_BACKEND_URL` and `ORC_CONNECTOR_TOKEN`, and the agent can reach your chat. Full env reference and the other tools are in the [`orc-mcp` README](connectors/orc-mcp/README.md).

## Run the backend (dev)

```bash
# Postgres for tests / dev
docker run -d --name orc-pg \
  -e POSTGRES_DB=openremote_control -e POSTGRES_USER=acc_user \
  -e POSTGRES_PASSWORD=acc_password -p 5544:5432 postgres:16

cd backend
uv sync --extra dev
POSTGRES_PORT=5544 POSTGRES_HOST=localhost .venv/bin/python -m pytest -q
```

> **Note:** `acc_user` / `acc_password` are throwaway local-dev credentials — they are **not** production secrets. Set a real `POSTGRES_PASSWORD` (and `SECRET_KEY`) via the environment before deploying.

See [`docker-compose.yml`](docker-compose.yml) and the [`Makefile`](Makefile) for the full dev loop.

## Stack

All free / OSS-friendly:

- **Backend** — Django 5.1, DRF, Channels, Celery 5
- **Data plane** — PostgreSQL 16, Valkey 8, append-only audit log
- **Surfaces** — Telegram, Matrix (→ WhatsApp/Slack/Signal via bridges), MCP; Next.js 15 + Tailwind + shadcn/ui PWA planned
- **Host side** — Python 3.13 daemon, age-encrypted credential vault, ntfy push, faster-whisper voice, Tailscale / headscale connectivity
- **Ops** — Docker Compose (dev), Caddy 2, OpenTelemetry → Loki + Tempo + Prometheus + Grafana

## Contributing

The backend foundation is solid; the most useful contributions right now are about **reaching more tools and standing it up live**. Best first contributions:

- **Add a runtime adapter** for another agent CLI — open a discussion describing its session lifecycle, auth modes, and the events/hooks it exposes.
- **Test the deploy path** on your own self-hosted infrastructure and report where the docs fall short — the runbook is being written now, and real-world friction is invaluable.
- **Improve the `orc-mcp` install docs** for Cursor, Codex, Claude Code, Copilot, and Kiro.
- **Harden the security boundaries** — the vault, policy engine, secrets redactor, and approval flow get design review before changes; security-minded eyes are very welcome. See [SECURITY.md](SECURITY.md) and [`docs/security/`](docs/security/).
- **Bug reports and tests** against the existing backend apps.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, CI, and commit conventions. Some early design notes still live in the maintainer's private knowledge base; if a decision is unclear, open an issue and we'll move the needed context into public docs.

## License

[Apache-2.0](LICENSE) for code. Documentation in [`docs/`](docs/) is dual-licensed CC-BY-SA 4.0 / Apache-2.0.

OpenRemote Control is an independent, third-party project, not affiliated with Anthropic, OpenAI, Google, or other supported tool vendors. Per Anthropic's Agent SDK guidelines it does not use the "Claude Code" name or visual style; in the UI, Claude runtimes are labeled "Claude Agent (SDK)", "Claude (Remote Control)", "Claude (CLI)", or "Claude (observed)" depending on the adapter.

## Author

Philippe Hermann · [Pher217](https://github.com/Pher217)
