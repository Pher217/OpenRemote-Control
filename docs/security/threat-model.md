# Threat Model

Scope: a single-operator, self-hosted OpenRemote-Control deployment (backend +
host daemon on machines the operator owns). This is the deployment shape
described in [SECURITY.md](../../SECURITY.md#trust-model--read-this-before-self-hosting)
— not multi-tenant.

## Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Telegram bot token | `TELEGRAM_BOT_TOKEN` env var, backend process | Whoever holds it can send/receive as the bot, i.e. read and write every chat the bot is in. |
| Host enrollment token | `HostToken.token_hash` (sha256 at rest, `backend/apps/hostlink/models.py`); raw value returned once at enroll time and held by the daemon | Authenticates a host daemon to the backend. Possession lets a party open the daemon's WebSocket and receive/send `host_command` frames. |
| The developer machine running the daemon | Wherever `agent_host` is enrolled | The daemon executes `claude` (by default with `--permission-mode bypassPermissions`) on this machine — it is the actual code-execution surface. |
| `TELEGRAM_ALLOWED_CHAT_IDS` / `TELEGRAM_FORUM_CHAT_ID` config | Backend `.env` | Defines who may drive sessions. Misconfiguration (empty allowlist bypassed, wrong forum id) widens who can act. |
| Audit log | Postgres, append-only | Post-incident record of who asked/approved what. |

## Trust boundaries

```
Telegram API  ──(bot token, HTTPS)──▶  Backend (Django/Channels)
                                           │  WS: HMAC(host_id:ts:nonce), nonce replay
                                           │  cache (backend/apps/hostlink/consumers.py)
                                           ▼
                                      Host daemon (agent_host)
                                           │  spawns/drives `claude` locally
                                           ▼
                                 Developer machine (trusted-host mode)
```

1. **Telegram ↔ Backend.** The bot token authenticates the backend to Telegram's
   API; there is no cryptographic proof the *sender* of an inbound message is
   who they claim beyond Telegram's own `from_user_id`. The backend gates on
   that id against `TELEGRAM_ALLOWED_CHAT_IDS` before acting
   (`backend/apps/telegram/service.py::handle_update`, `handle_forum_reply`) —
   default-deny: `TELEGRAM_ALLOWED_CHAT_IDS` is an empty set unless explicitly
   configured (`backend/config/settings/base.py`).
2. **Backend ↔ Host daemon.** Each host enrolls once via `/enroll` to obtain a
   per-host token (`HostToken.issue`); only the sha256 hash is stored server-side,
   the raw value is shown once. Every WebSocket connection is signed:
   `signature = HMAC-SHA256(token, "{host_id}:{ts}:{nonce}")`, verified with a
   constant-time compare and a 300s clock-skew window
   (`backend/apps/hostlink/security.py::verify_sig`). The `(host_id, nonce)` pair
   is additionally recorded in the cache (`cache.add`) so a captured URL cannot
   be replayed after first use (`backend/apps/hostlink/consumers.py::connect`).
3. **Host daemon → local execution.** The daemon runs `claude` locally. The
   default engine (`ORC_HEADLESS_ENGINE=interactive` or unset) passes
   `--permission-mode bypassPermissions` — **no per-tool approval gate**. This
   is "trusted-host mode": once a message clears the Telegram allowlist gate,
   it can trigger arbitrary tool use on the host. Setting
   `ORC_HEADLESS_ENGINE=sdk` instead routes each tool call through an explicit
   Allow/Deny prompt in chat (`backend/apps/hostlink` approval flow +
   `host-agent/agent_host/sdk_session.py`), trading latency for a real
   per-action gate.

## Residual risks (accepted, not bugs)

- **Anyone on `TELEGRAM_ALLOWED_CHAT_IDS` can execute code on every enrolled
  host.** The allowlist is an identity gate, not a per-action review. Keep it
  to the smallest set of Telegram user ids that actually need to drive
  sessions.
- **A compromised Telegram account of an allowlisted user is equivalent to
  code execution** on the host(s) it can reach — there is no additional
  factor beyond Telegram's own account security.
- **A leaked bot token lets an attacker impersonate the bot** in every chat it
  is a member of (read history, send messages) until the token is rotated via
  BotFather and the new value redeployed.
- **A leaked host enrollment token lets an attacker open the daemon's
  WebSocket** until the token is rotated (`HostToken.issue` revokes the prior
  active token for that host automatically on re-issue).
- **No multi-tenant isolation.** There is one allowlist and one forum per
  backend; this deployment shape is explicitly single-operator
  (see [SECURITY.md](../../SECURITY.md) "Do not deploy this multi-tenant").
- **Telegram API and infrastructure outside the Docker Compose / documented
  deploy paths are out of scope** — see `SECURITY.md`'s Scope section.

## Recommendations

- Use a **dedicated bot token** for this deployment — never reuse a bot token
  shared with another service.
- Keep `TELEGRAM_ALLOWED_CHAT_IDS` to the minimum set of user ids that need to
  drive sessions; verify it is not left unset (unset silently denies all,
  which is safe, but confirm this is intentional rather than a config gap
  someone will "fix" open).
- Rotate the bot token and any host enrollment tokens immediately on any
  suspicion of compromise (lost device, leaked `.env`, unexpected chat
  activity).
- Prefer `ORC_HEADLESS_ENGINE=sdk` over the default when driving a host whose
  blast radius from an errant or malicious command would be high.
