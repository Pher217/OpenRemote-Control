# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is pre-1.0
and the API is not yet stable.

## [Unreleased]

### Added — Universal Aggregator backend (code-complete, ~438 tests passing)
- Multi-runtime **observe** for Claude Code, Codex, and Gemini via a pluggable runtime registry.
- Interactive **answer-in-chat** via the `Prompt` primitive.
- The **universal MCP bridge** — `apps.connectors` backend plus the installable
  [`orc-mcp`](connectors/orc-mcp/README.md) client (`notify`, `ask_human`, `request_approval`),
  usable from any MCP-capable tool.
- **Telegram + Matrix** surfaces (Matrix bridges fan out to WhatsApp / Slack / Signal).
- **Multi-host** backend (`apps.hostlink`) and a host daemon client (`host-agent`:
  `orc-host enroll | daemon`).
- A **PTY input-safety core** for future approval-gated terminal control.

### Changed
- Routed security reports through GitHub private vulnerability reporting instead of a placeholder email.
- Rewrote the README to reflect the shipped universal aggregator and MCP bridge.

### Not yet built
- Deploy runbook (Matrix homeserver + bridges + headscale; daemon on a second machine).
- `orc run` approval-gated remote terminal (PTY) streaming.
- Per-connector keypair identity hardening (replacing the shared bearer token) before multi-user use.
- The Next.js PWA frontend (scaffolded only).

## [0.0.1-spec]
- Specification phase; no runnable code.
