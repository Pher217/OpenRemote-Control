# OpenRemote Control

> Private, local-first cockpit for supervising AI agents across machines, projects, and runtimes — one mobile-first inbox where every coding CLI, model API, voice agent, or business agent appears as a thread, controllable with universal slash commands, with parallel sessions across multiple machines and accounts.

**Status:** Phase 1 backend implementation in progress. Django scaffold, models, API layer, audit signals, policy permissions, WebSocket consumer, and Celery tasks are implemented with 72 passing tests.

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

## Status

Backend Phase 1 is in place: Django apps (accounts, hosts, projects, policies, threads, approvals, audit, skills, slash, adapters, tier2, telegram) with models, DRF API, a Channels WebSocket consumer, Celery tasks, audit signals, and policy permissions. The credential envelope (key id, recipient, scheme version, rotation, host binding, revocation) and redaction-before-persistence are part of the data model. Runtime adapters, the host daemon, and the frontend are not built yet.

Design specifications, the delegated task breakdown, and the threat-model planning are maintained in the maintainer's private knowledge base rather than in this repo.

## Branding

This project is a third-party tool. Per Anthropic's Agent SDK guidelines, this product does not use the "Claude Code" name or visual style. In UI, Claude runtimes are labeled "Claude Agent (SDK)", "Claude (Remote Control)", "Claude (CLI)", or "Claude (observed)" depending on adapter.

## License

[Apache-2.0](LICENSE) for code. Documentation in `docs/` is dual-licensed CC-BY-SA 4.0 / Apache-2.0.

## Author

Philippe Hermann · [Pher217](https://github.com/Pher217)
