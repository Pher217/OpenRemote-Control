# Security Policy

## Supported Versions

| Version | Status |
|---|---|
| < 0.1.0-alpha | Not supported — spec / pre-release only |
| 0.1.0-alpha and later | Supported once released |

Only the latest minor version within a supported major line receives security patches.

## Reporting a Vulnerability

**Please do not open public issues for security vulnerabilities.**

Email: `security@pher217.dev` (PGP key available on [OpenPGP key servers](https://keys.openpgp.org)).

If you do not receive an acknowledgment within **48 hours**, or if the issue is critical and you believe we may have missed it, follow up with a second email and prefix the subject line with `[URGENT]`.

### What to include

- A clear description of the vulnerability and its impact.
- Steps to reproduce, ideally as a minimal test case or script.
- The affected component, version, and configuration.
- Any proposed mitigation or patch.

### What NOT to include

- **Never send credentials, API keys, tokens, passwords, or private keys** in the report.
- If logs or screenshots contain secrets, redact them before sending.
- If you need to share a reproduction that requires secrets, say so in the report and we will arrange a secure transfer channel.

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
- The Next.js PWA frontend (`frontend/`).
- Authentication flows, session management, and credential storage.
- Policy enforcement, approval workflows, and audit pipelines.
- Runtime adapters and PTY supervision.
- Docker Compose and deployment configurations.

Out of scope:

- Third-party dependencies unless the vulnerability is directly exploitable through our integration surface.
- Infrastructure outside our Docker Compose and documented deployment paths.
- Social engineering attacks against individual users.
- LLM provider platforms (Anthropic, OpenAI, Google, etc.) — report to them directly.

## Security-Related Resources

- Threat model: [`docs/security/threat-model.md`](docs/security/threat-model.md)
- Security checklist: [`docs/security/security-checklist.md`](docs/security/security-checklist.md)
- Security gates and acceptance tests: See the T-104 deliverables in `docs/security/`.
