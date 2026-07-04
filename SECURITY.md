# Security Policy

## Supported Versions

| Version | Status |
|---|---|
| < 0.1.0-alpha | Not supported — spec / pre-release only |
| 0.1.0-alpha and later | Supported once released |

Only the latest minor version within a supported major line receives security patches.

## Reporting a Vulnerability

**Please do not disclose security vulnerabilities in public issues, pull requests, or discussions.**

Report privately using **GitHub's private vulnerability reporting**:

1. Go to the [Security tab](https://github.com/Pher217/OpenRemote-Control/security) of this repository.
2. Click **Report a vulnerability** to open a private security advisory visible only to the maintainers.

This keeps the report confidential until a fix is released. If private reporting is unavailable to you, open a regular [GitHub issue](https://github.com/Pher217/OpenRemote-Control/issues/new) that says only "requesting a security contact" — **without any vulnerability details** — and a maintainer will follow up.

If you do not receive an acknowledgment within **48 hours**, or the issue is critical, add a comment to your advisory (or follow-up issue) prefixed with `[URGENT]`.

### What to include

- A clear description of the vulnerability and its impact.
- Steps to reproduce, ideally as a minimal test case or script.
- The affected component, version, and configuration.
- Any proposed mitigation or patch.

### What NOT to include

- **Never include credentials, API keys, tokens, passwords, or private keys** in the report.
- If logs or screenshots contain secrets, redact them first.
- If a reproduction requires secrets, say so in the advisory and we will arrange a secure transfer channel.

## Response Timeline

| Phase | Target |
|---|---|
| Initial acknowledgment | 48 hours |
| Severity assessment and reproduction confirmation | 5 business days |
| Patch development (Critical / High) | 14 calendar days |
| Patch development (Medium / Low) | 30 calendar days |
| Coordinated disclosure after fix release | 90 days from acknowledgment, or sooner by mutual agreement |

We follow a [coordinated vulnerability disclosure](https://cheatsheetseries.owasp.org/cheatsheets/Vulnerability_Disclosure_Cheat_Sheet.html) process. We ask that reporters give us a reasonable time to address the issue before disclosing it publicly.

## Scope

In scope:

- The Django backend (`backend/`).
- The host daemon (`host-agent/`).
- The connectors (`connectors/orc-mcp/`, `connectors/messaging-gateway/`).
- Authentication flows, session management, and credential storage.
- Policy enforcement, approval workflows, and audit pipelines.
- Runtime adapters and PTY supervision.
- Docker Compose and deployment configurations.

Out of scope:

- Third-party dependencies unless the vulnerability is directly exploitable through our integration surface.
- Infrastructure outside our Docker Compose and documented deployment paths.
- Social engineering attacks against individual users.
- LLM provider platforms (Anthropic, OpenAI, Google, etc.) — report to them directly.

## Trust Model — read this before self-hosting

OpenRemote-Control's core feature is **remote code execution by design**: a message in
your chat surface drives a coding agent on your machine. The default engine runs
`claude` with `--permission-mode bypassPermissions` — no per-tool approval gate.
This is **trusted-host mode**, and it is a deliberate trade-off, not an oversight:

- The threat model assumes a **single operator, self-hosting on a machine they own**,
  driving **their own** coding sessions from **their own** chat account.
- The gate is **identity, not per-action review**: on the **Telegram surface**,
  only user ids in `TELEGRAM_ALLOWED_CHAT_IDS` can drive sessions, and the
  allowlist **defaults to deny-all** when unset. Host daemons must enroll and
  sign every WebSocket connection (HMAC over `host_id:ts:nonce`, constant-time
  compare, nonce replay rejection — `backend/apps/hostlink/security.py`).
- The **messaging-gateway surface** (WhatsApp/Slack/Discord/Signal/iMessage) is
  gated by the sidecar's bearer token, **not** by the Telegram allowlist —
  treat that token as equivalent to an allowlist entry, and do not enable
  gateway surfaces until you have reviewed who can message the linked accounts
  (see the threat model for the open review item on this path).
- Consequences you accept: **anyone on the allowlist can execute code on the
  enrolled host**, and a compromised Telegram account of an allowlisted user
  equals code execution. Keep the allowlist minimal, use a dedicated bot token,
  and rotate it on any suspicion.
- If you want a per-action gate, run `ORC_HEADLESS_ENGINE=sdk`, which routes every
  tool call through an Allow/Deny prompt in the chat before it executes.

**Do not deploy this multi-tenant.** There is no per-connector "drive" scope yet;
the design target is one operator per backend.

## Security-Related Resources

- Threat model: [`docs/security/threat-model.md`](docs/security/threat-model.md)
- Security checklist: [`docs/security/security-checklist.md`](docs/security/security-checklist.md)
