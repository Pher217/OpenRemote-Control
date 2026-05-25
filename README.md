# Agent Command Center

> Private, local-first cockpit for supervising AI agents across machines, projects, and runtimes — one mobile-first inbox where every coding CLI, model API, voice agent, or business agent appears as a thread, controllable with universal slash commands, with parallel sessions across multiple machines and accounts.

**Status:** specification phase. No runnable code yet.

## What this is

A control plane that sits above existing AI agent runtimes — Claude (via Agent SDK or CLI), Codex, Ollama, OpenCode, Aider — and exposes them through one unified chat UX (PWA + Telegram), with policy, approval, and audit layers built in.

Defining characteristics:

- **Multi-host** — your MacBook, your Windows workstation, your Linux VPS, all in one fleet
- **Multi-account per provider** — separate Anthropic accounts for personal vs. Schatzi work, each scoped per thread
- **Policy engine** — sensitivity-aware project profiles that block cloud agents on confidential repos before launch
- **Mobile-first approval inbox** — risk-tiered approvals via push, not a terminal viewer
- **Queryable audit log** — append-only Postgres across heterogeneous runtimes
- **Two surfaces, one backend** — PWA (Add to Home Screen) and Telegram bot, both backed by the same Django + Channels backend

## What this is not

Not a new AI agent. Not a replacement for Claude Code, Codex, or Cursor. Not a hosted SaaS. Not a workflow builder. Not an IDE.

## Reachability honesty

| Tier | Examples | Reachable? |
|---|---|---|
| 1. Local CLIs | Claude Code, Codex, Ollama, OpenCode, Aider | ✅ via Agent SDK / PTY / HTTP |
| 2. Provider APIs | Anthropic, OpenAI, Gemini, ElevenLabs, OpenRouter, Salesforce Agentforce | ✅ standard HTTPS |
| 3. Closed third-party UIs | Cursor, Windsurf, Antigravity, Copilot in Excel/Word/PowerPoint, claude.ai web, chatgpt.com web | ❌ vendor-blocked |

This project covers Tiers 1 and 2. Tier 3 is out of scope — those vendors do not expose chat-injection APIs and we will not ship browser-extension hacks.

## Stack (all free / OSS-friendly)

Django 5.1 + DRF + Channels · PostgreSQL 16 · Valkey 8 · Celery 5 · Next.js 15 PWA + Tailwind + shadcn/ui · Python 3.13 host daemon · age-encrypted credential vault · ntfy push · python-telegram-bot · faster-whisper for voice · Tailscale (or headscale) for connectivity · Caddy 2 · OpenTelemetry → Loki + Tempo + Prometheus + Grafana · Docker Compose for dev.

Full stack rationale: see [docs/specs/agent-command-center-v5-build-spec.md](docs/specs/agent-command-center-v5-build-spec.md) §1.2.

## Documents

This repo currently ships only the specification. Read in order:

1. [V5 build spec](docs/specs/agent-command-center-v5-build-spec.md) — full stack, data model, ~50 delegation-tagged tasks
2. [V5 addendum](docs/specs/agent-command-center-v5-addendum.md) — Agent SDK, Agent View, Telegram, OpenClaw, Hermes, OpenCode-RC
3. [Codex review of V5](docs/specs/2026-05-25-codex-review-of-v5-spec.md) — 3 blockers, 9 highs, 6 mediums; V6 patch table
4. [Market review](docs/specs/2026-05-25-agent-command-center-market-review.md) — May 2026 competitor scan
5. [V4 universal cockpit](docs/specs/agent-command-center-v4-universal-cockpit.md) — Beeper-style chat aggregator reframe (predecessor)
6. [V3 governance overlay](docs/specs/agent-command-center-v3-sharpened.md) — narrower scope alternative
7. [V2 archived](docs/specs/agent-command-center-v2-archived.md) — original deep-research document

## Status of the spec

V5 is drafted but **not yet patched** to address Codex's 3 blockers:

- **B1.** `pyte`-based PTY supervision can't reliably recover semantic chat turns → migrate to Claude Agent SDK + `claude -p --output-format stream-json`.
- **B2.** Account vault is missing envelope metadata (`key_id`, `recipient`, `scheme_version`, rotation, host binding, revocation).
- **B3.** Secrets land in `Message.content` / `AuditEvent.payload` before redaction — must redact at receive-time, store redacted by default, encrypt raw separately.

No code will be written against V5 until V6 lands. Patch plan is in the Codex review document above.

## Branding

This project is a third-party tool. Per Anthropic's Agent SDK guidelines, this product does not use the "Claude Code" name or visual style. In UI, Claude runtimes are labeled "Claude Agent (SDK)", "Claude (Remote Control)", "Claude (CLI)", or "Claude (observed)" depending on adapter.

## License

[Apache-2.0](LICENSE) for code. Documentation in `docs/` is dual-licensed CC-BY-SA 4.0 / Apache-2.0.

## Author

Philippe Hermann · [Pher217](https://github.com/Pher217)
