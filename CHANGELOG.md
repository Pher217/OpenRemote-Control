# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is pre-1.0
and the API is not yet stable.

## [Unreleased]

## [0.1.0] - 2026-07-12

First tagged release. Universal cross-tool `/remote-control`: dispatch any MCP-capable coding agent (Claude Code, Codex, and others) to a chat topic you already supervise, drive it from there, and stay in sync across every machine you enrol.

### Added — public launch & onboarding
- [#108](https://github.com/Pher217/OpenRemote-Control/pull/108) — one-command `quickstart.sh` onboarding.
- [#105](https://github.com/Pher217/OpenRemote-Control/pull/105) / [#106](https://github.com/Pher217/OpenRemote-Control/pull/106) / [#109](https://github.com/Pher217/OpenRemote-Control/pull/109) / [#115](https://github.com/Pher217/OpenRemote-Control/pull/115) — README repositioned as universal/cross-tool/one-inbox; status badges, demo GIF/screenshot.
- [#98](https://github.com/Pher217/OpenRemote-Control/pull/98) — OSS-readiness audit closed: green CI, security docs, no personal data.
- [#99](https://github.com/Pher217/OpenRemote-Control/pull/99) / [#100](https://github.com/Pher217/OpenRemote-Control/pull/100) — CI hardening (Valkey healthcheck, last lint violations) — main CI green.
- Repo went public: branch protection, secret scanning, and private vulnerability reporting enabled.

### Added — Codex support
- [#110](https://github.com/Pher217/OpenRemote-Control/pull/110) — drive Codex sessions from chat (`codex exec resume`-based engine, mirroring the Claude interactive engine).
- [#111](https://github.com/Pher217/OpenRemote-Control/pull/111) — bind `/openremote-control` to your current Codex session (forked-snapshot handoff, with an explicit caveat that the editor won't see phone-driven turns).

### Added — multi-host
- [#112](https://github.com/Pher217/OpenRemote-Control/pull/112) — configurable daphne bind host (`ORC_BIND_HOST`) for LAN reachability from a second machine.
- [#113](https://github.com/Pher217/OpenRemote-Control/pull/113) — route driveable sessions to the caller's own host by hostname match, fixing dispatch silently breaking the moment a second host enrols.

### Fixed — dispatch reliability
- [#114](https://github.com/Pher217/OpenRemote-Control/pull/114) — dedup `/openremote-control` re-dispatch for an already-running session (was creating a second Telegram topic per redispatch); silent Telegram-topic auto-recreation now logs and checks for a sibling owner instead of minting a duplicate; capability-based drive gating (`CLAUDE_CODE_ENTRYPOINT`) marks VSCode-extension-hosted sessions non-driveable instead of silently offering broken write-drive.
- [#116](https://github.com/Pher217/OpenRemote-Control/pull/116) — closes a topic-creation race the #114 advisory lock didn't cover (two concurrent dispatches for one session could still both create a topic); tightens the sibling-topic-owner check to active threads in the same forum.

### Added — write+stream driving wave (PRs #91–#97, ~800 tests passing)
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
