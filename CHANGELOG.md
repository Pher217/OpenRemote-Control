# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is pre-1.0
and the API is not yet stable.

## [Unreleased]

### Added — 2026-07-04: write+stream driving wave (PRs #91–#97, ~800 tests passing)
- [#91](https://github.com/Pher217/OpenRemote-Control/pull/91) — `/openremote-control` can drive *this* coding session by resuming `CLAUDE_CODE_SESSION_ID`.
- [#93](https://github.com/Pher217/OpenRemote-Control/pull/93) — scoped transcript tail mirrors editor-typed turns to chat (session-bridge phase 1).
- [#94](https://github.com/Pher217/OpenRemote-Control/pull/94) — user turns stay visible through drive suppression; pending threads resync.
- [#95](https://github.com/Pher217/OpenRemote-Control/pull/95) — persistent interactive engine (`ORC_HEADLESS_ENGINE=interactive`): one long-lived `claude -p` stream-json process per session, no per-turn respawn.
- [#96](https://github.com/Pher217/OpenRemote-Control/pull/96) — fresh interactive engines resume existing sessions (restart-survival gate fix).
- [#97](https://github.com/Pher217/OpenRemote-Control/pull/97) — Telegram bot liveness watchdog self-heals a silent `getUpdates` stall.
- [#92](https://github.com/Pher217/OpenRemote-Control/pull/92) — dependency bump: `redis` requirement widened to `>=5.0,<9` in `backend`.

### Added — Universal Aggregator backend (code-complete)
- Multi-runtime **observe** for Claude Code, Codex, and Gemini via a pluggable runtime registry. (Removed — see below.)
- Interactive **answer-in-chat** via the `Prompt` primitive.
- The **universal MCP bridge** — `apps.connectors` backend plus the installable
  [`orc-mcp`](connectors/orc-mcp/README.md) client (`notify`, `ask_human`, `request_approval`),
  usable from any MCP-capable tool.
- **Telegram** surface; the messaging-gateway connector bridges WhatsApp / Slack / Discord / Signal / iMessage **directly, with no Matrix relay**.
- **Multi-host** backend (`apps.hostlink`) and a host daemon client (`host-agent`:
  `orc-host enroll | daemon`).
- A **PTY input-safety core** for future approval-gated terminal control.

### Removed
- The multi-runtime, read-only `observe` watcher and its per-agent runtime adapters
  (`apps.observe.runtimes`) — every chat is now write+stream by default, so the
  read-only path was dead weight. `observe` now only holds turn persistence and
  chat delivery for driven sessions ([PR #90](https://github.com/Pher217/OpenRemote-Control/pull/90)).

### Changed
- Routed security reports through GitHub private vulnerability reporting instead of a placeholder email.
- Rewrote the README to reflect the shipped universal aggregator and MCP bridge.

### Not yet built
- Deploy runbook (docker-compose: backend + messaging-gateway sidecar + headscale; daemon on a second machine).
- `orc run` approval-gated remote terminal (PTY) streaming.
- Per-connector keypair identity hardening (replacing the shared bearer token) before multi-user use.
- The Next.js PWA frontend (scaffolded only).

## [0.0.1-spec]
- Specification phase; no runnable code.
