---
type: spec-addendum
project: Ideas
parent_spec: agent-command-center-v5-build-spec
version: v5-addendum-1
date: 2026-05-25
status: draft — merges into V5 on next revision
---

# V5 Addendum #1 — Agent SDK, Agent View, Telegram, OpenClaw, Hermes, OpenCode-RC

> **Why this addendum.** Five things surfaced after V5 was written that materially change the build:
>
> 1. **Claude Agent SDK** (Python + TypeScript) — official, programmable, replaces most of the PTY supervision strategy.
> 2. **Claude Code Agent View** (`claude agents`, May 11 2026) — Anthropic's own parallel-session dashboard, including a JSONL session store at `~/.claude/projects/<project>/<session-id>.jsonl` that we CAN read externally (V5 §0 said we couldn't — wrong).
> 3. **lesquel/open-remote-control** — reference impl for OpenCode remote-control with embedded HTTP+SSE, Telegram bot, Cloudflare tunnel; MIT, ~9000 LOC, 228 tests. Integrate rather than rebuild.
> 4. **OpenClaw** — viral (100K+ stars Feb 2026) open-source AI agent in messaging apps (Signal/Telegram/Discord/WhatsApp). Now a non-profit foundation. Treat as a meta-runtime adapter.
> 5. **Hermes Agent (Nous Research)** — open-source persistent-memory agent with Telegram/Discord/Slack/WhatsApp/Signal/Email gateways. python-telegram-bot under the hood. Treat as a meta-runtime.
>
> Plus: Telegram becomes a first-class cockpit surface (not just a notification target), and a small but important branding rule from Anthropic.

---

## A. Correction: Claude Code DOES expose local session state

V5 §0 row 3 said *"RC exposes IPC we can read — No. Outbound HTTPS only. No documented local files for session state."*

That was correct **for the Remote Control bridge specifically**. But the broader Claude Code product does store sessions on disk:

> **Transcripts are stored as JSONL at `~/.claude/projects/<project>/<session-id>.jsonl`**, where `<project>` is derived from your working directory path. Each line is a JSON object for a message, tool use, or metadata entry. To store sessions somewhere other than `~/.claude`, set `CLAUDE_CONFIG_DIR`. These local files are removed after 30 days by default; change this with `cleanupPeriodDays`. — [official docs](https://code.claude.com/docs/en/sessions)

**This unlocks Strategy D** (new — see §B.4 below).

---

## B. Claude integration — four strategies, ranked

V5 had two strategies (A=RC-link, B=PTY). Replace with this ranking:

### B.1 Strategy C (NEW, primary) — Claude Agent SDK

**Mechanism.** Backend imports `claude-agent-sdk` (Python) or `@anthropic-ai/claude-agent-sdk` (TypeScript). For each cockpit message, calls `query(prompt, options)` and yields the async iterator of messages directly into the WebSocket consumer.

```python
from claude_agent_sdk import query, ClaudeAgentOptions
async for message in query(
    prompt=user_text,
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash", "Glob", "Grep"],
        cwd=thread.worktree_path,
        resume=thread.external_session_ref,   # session id captured on first turn
        hooks={
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[command_classifier_hook])],
            "PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[audit_hook])],
        },
        permission_mode="default",  # tools require explicit approval
    ),
):
    await consumer.send_event(normalize(message))
```

**What we get.**
- Streaming, structured messages out of the box. No PTY, no pyte, no ANSI stripping.
- **Hooks** (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`) plug directly into our policy + audit layer.
- Skills, slash commands, CLAUDE.md all load from `.claude/` automatically.
- MCP server config passes through.
- Subagent support (we can declare `code-reviewer` etc inside the call).
- Session resume by ID (`session_id` is yielded on `SystemMessage subtype="init"`).
- TypeScript SDK bundles a native Claude Code binary — no separate install needed.
- Works against API key, Bedrock, Claude Platform on AWS, Vertex, Azure Foundry.

**What we lose / constraints.**
- **API key auth only** for OSS distribution. [Anthropic explicitly forbids](https://code.claude.com/docs/en/agent-sdk/overview): *"Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK."* — users bring their own API key.
- **Starting June 15, 2026**: Agent SDK + `claude -p` on subscription plans draw from a new monthly Agent SDK credit, separate from interactive usage. Document this clearly for users.
- Branding rule (see §G).

**Recommendation.** Strategy C becomes the **default** Claude adapter. PTY supervision (V5 Strategy B) is demoted to fallback for users who specifically want to drive the interactive CLI.

### B.2 Strategy A (kept) — RC-link adapter

Unchanged from V5 §2.1. Still useful when the user wants a Claude-app/claude.ai/code-deep-link experience for a long-running session with provider push notifications, especially on subscription plans where Agent SDK quota is constrained.

### B.3 Strategy B (demoted) — PTY supervisor

V5's Strategy B. Now optional. Use only when:
- The user must run `claude` with subscription auth (no API key) and doesn't want RC.
- The user wants to mirror an interactive terminal session into the cockpit chat view.

Likely deprecated within 6 months as Agent SDK matures.

### B.4 Strategy D (NEW, secondary) — JSONL watcher for Agent View sessions

**Mechanism.** Host daemon watches `~/.claude/projects/` (or `$CLAUDE_CONFIG_DIR/projects/`) for new/modified `.jsonl` files. For each new file → register a Thread of `runtime_mode="external_observed"`. Stream messages by tailing the JSONL. To send a message into an externally-started session: spawn `claude --resume <session-id> -p "<message>"` headless.

**What we get.** Sessions started via `claude agents`, `claude` CLI, the desktop app, or VS Code show up automatically in our cockpit inbox alongside cockpit-originated threads. The user sees one unified inbox even when working natively in Claude.

**What we lose.** Read-mostly. Sending into a session requires spawning a subprocess each time (acceptable). Cannot intercept tool calls in real time (those happen in the foreign process).

**File-watching libs:**
- Linux: `inotify` via `watchdog`
- macOS: FSEvents via `watchdog`
- Windows: `ReadDirectoryChangesW` via `watchdog`

`watchdog` (BSD, mature) unifies all three.

### B.5 Picker

```text
Project sensitivity   Mobile push wanted   Recommended Claude strategy
public/internal       no                   C — Agent SDK (default)
public/internal       yes                  A — RC-link
confidential/reg.     no                   C — Agent SDK with API key (sovereignty)
confidential/reg.     yes                  A — RC-link + sandbox flag
(External agents)     n/a                  D — JSONL watcher only
```

---

## C. Telegram as a first-class cockpit surface

V5 had Telegram only as a notification target. Promote it.

### C.1 Why

- **OpenClaw (100K+ stars) and Hermes Agent** prove Telegram is where users actually live for AI chat. Both ship Telegram as primary UX.
- Telegram Bot API 9.4 (Feb 2026) added **Private Chat Topics** — one bot can carry many parallel thread topics in a single 1-on-1 DM. Perfect for the thread-inbox model.
- Bot API 9.5 (Mar 2026) added **native streaming via sendMessageDraft** — incremental message updates without spam.
- iOS/Android push is solved (Telegram handles it).
- Voice memos auto-transcribe (Hermes pattern).
- No PWA install friction.

### C.2 Design

A new app `apps/telegram/` in the backend:

```
apps/telegram/
├── bot.py              # python-telegram-bot Application
├── handlers/
│   ├── new_thread.py   # /new <runtime> <project>
│   ├── messages.py     # text/voice/photo → route to thread
│   ├── slash.py        # cockpit /commands (/branch, /approve, etc.)
│   ├── approval.py     # inline keyboard for approval requests
│   └── inbox.py        # /inbox, /threads, /pin
├── thread_mapper.py    # Telegram topic ↔ Thread mapping
├── transcribe.py       # Whisper API for voice memos
└── auth.py             # link Telegram user to Django user
```

### C.3 Stack additions

| Layer | Choice | License | Why |
|---|---|---|---|
| Telegram bot framework | **python-telegram-bot 22.x** | LGPL-3.0 | Mature, async, what Hermes uses. LGPL is compatible with Apache-2.0 for use (not for linking modification — fine here, we use as library). |
| Voice transcription | **faster-whisper** (CTranslate2) + local Whisper-large | MIT / MIT | Free, runs on CPU/GPU/Mac MPS. No OpenAI API needed. |
| Streaming draft | hand-rolled on top of `editMessageText` | n/a | Bot API 9.5 streaming requires explicit support; use editMessageText fallback if SDK lags. |

### C.4 UX mapping

- **One Telegram DM = one user**. Bot creates a Topic per Thread (Bot API 9.4 feature). Topic name = thread name.
- **Sending text in a topic** → message routed to that thread's runtime.
- **Voice memo in a topic** → transcribed via faster-whisper → routed.
- **Replying to a bot approval message with the inline ✅/❌ buttons** → approve/reject.
- **`/new claude-code schatzi-api`** (in main DM) → creates a new thread + topic.
- **`/threads`** → list, **`/pin <id>`** → pin, **`/stop <id>`** → stop.
- **`/branch feature-x`** in a topic → universal slash middleware (same as PWA).

### C.5 Notification fan-out

The backend's notification subsystem (already NTFY-based in V5) gets a new transport: Telegram. PolicyProfile chooses per-event: `push_via: [pwa, telegram, ntfy, none]`.

---

## D. OpenClaw / Hermes Agent as meta-runtime adapters

These two are not regular runtimes — they ARE control planes themselves. Treat them as upstream agent runtimes the cockpit can dispatch into.

### D.1 OpenClaw adapter

**Why.** Users who already run OpenClaw have it configured with their model providers, integrations, and habits. Forcing them to abandon it is a non-starter. Instead, route cockpit threads INTO OpenClaw via its programmatic interface.

**Mechanism.**
- OpenClaw exposes a local HTTP API (port configurable). Probe at `http://localhost:<port>/health`.
- New thread of runtime=`openclaw` → POST `/sessions` with system prompt, model, allowed integrations.
- Send message → POST `/sessions/{id}/messages` (streaming SSE).
- Stop → POST `/sessions/{id}/stop`.
- Audit hooks: OpenClaw plugins fire on tool use; subscribe via WebSocket if exposed, else poll session log.

**Status.** OpenClaw API surface evolving fast (project is < 1 year old). Pin to OpenClaw version, feature-detect, fail gracefully.

### D.2 Hermes Agent adapter

**Why.** Same logic — Hermes has its own user base on Nous Research stack. Don't force migration; coexist.

**Mechanism.**
- Hermes runs as a gateway daemon on the host (configurable Telegram/Discord/Slack/WhatsApp/Signal/Email).
- Cockpit thread of runtime=`hermes` → SSH or local API into the Hermes gateway, dispatch task.
- Streaming via the Hermes WebSocket if available, else poll.
- For users who want Hermes-as-the-AI but cockpit-as-the-control-pane: cockpit becomes "yet another Hermes gateway" by speaking Hermes's gateway protocol back to it.

**Status.** Hermes is more API-stable than OpenClaw (Nous has been at this longer).

### D.3 Both are optional Phase 3 adapters

These are not MVP. They open in Phase 3 once core cockpit is stable. Adding the adapter contract for them is mostly: HTTP client + per-session state + audit translation.

---

## E. OpenCode adapter — wrap lesquel/open-remote-control instead of PTY

V5 §4 had OpenCode as PTY-only (Strategy B clone). Replace with:

### E.1 Mechanism

Install [`open-remote-control`](https://github.com/lesquel/open-remote-control) as the OpenCode plugin on each host. It embeds an HTTP+SSE server inside the OpenCode process. The cockpit's host daemon talks to that local HTTP server instead of PTY-supervising:

- `GET /sessions` → list active sessions
- `POST /sessions/{id}/messages` → send message (text/prompt)
- `GET /sessions/{id}/events` → SSE stream of message events
- `POST /codex/hooks/{event}` → already implements a Codex CLI bridge — we piggyback on this for our Codex Strategy D-equivalent
- Permission requests → cockpit becomes the approval consumer

### E.2 What we get for free

- Vanilla-JS dashboard already exists (we don't use it; we mirror its UX in our PWA).
- Telegram bot exists — we ignore it (we have our own Telegram surface in §C).
- QR pairing exists — we ignore it (our Tailscale auth is stricter).
- Cloudflare tunnel exists — we ignore it (Tailscale-only).

### E.3 What we add on top

- Multi-host: route OpenCode requests to the right host's local HTTP via host daemon proxy.
- Policy engine: command classifier intercepts before forwarding approval.
- Audit: every event mirrored into our Postgres AuditLog.
- Account binding: thread → Account → model API key, vs OpenCode's own model config.

### E.4 License compatibility

`open-remote-control` is MIT. Compatible with our Apache-2.0 distribution as a dependency. We do not vendor it; we depend on it (`bun install open-remote-control` per host).

---

## F. Updated stack additions

Append to V5 §1.2:

| Layer | Choice | License | Why |
|---|---|---|---|
| Claude Agent SDK (Python) | **`claude-agent-sdk`** | MIT | Strategy C primary path. |
| Claude Agent SDK (TypeScript, optional for backend) | **`@anthropic-ai/claude-agent-sdk`** | MIT | If we ship a TS-side worker for Tier 2 streaming, bundles Claude Code binary. |
| File system watcher | **`watchdog`** | Apache-2.0 | inotify/FSEvents/RDCW for Strategy D. |
| Telegram bot framework | **`python-telegram-bot` 22.x** | LGPL-3.0 | Mature, async; same as Hermes Agent uses. |
| Voice transcription | **`faster-whisper`** + Whisper-large model | MIT | Free, local. |
| OpenCode runtime | **`open-remote-control`** plugin (lesquel) | MIT | Adapter for OpenCode (§E). |
| OpenClaw runtime (Phase 3) | OpenClaw daemon + cockpit HTTP adapter | OpenClaw is open-source (license TBD) | Phase 3, see §D.1. |
| Hermes Agent runtime (Phase 3) | Hermes daemon + cockpit HTTP adapter | open-source | Phase 3, see §D.2. |

### F.1 License watch

- **python-telegram-bot LGPL-3.0**: linking is fine for an Apache-2.0 host (we use it as a library, no static linking into our binary). Document this carefully in `LICENSES.md`. If contributors object, alternative is `aiogram` (MIT).
- **OpenClaw license**: project-specific (verify before Phase 3). If non-permissive, treat as runtime dependency only, do not import code.

---

## G. Branding rule (Anthropic-imposed)

From the [Agent SDK docs](https://code.claude.com/docs/en/agent-sdk/overview):

> **Allowed:** "Claude Agent" (preferred for dropdown menus), "Claude" (when within a menu already labeled "Agents"), "{YourAgentName} Powered by Claude".
>
> **Not permitted:** "Claude Code" or "Claude Code Agent", Claude Code-branded ASCII art or visual elements that mimic Claude Code.

**Implication for V5 repo.**

- Repo name candidates to avoid: anything containing "claude code" or "cc-...". Stay with `agent-command-center` or `cockpit-ai`.
- In UI: runtime selector should label this runtime as **"Claude Agent (SDK)"** for Strategy C, **"Claude (Remote Control)"** for Strategy A, **"Claude (CLI)"** for Strategy B, **"Claude (observed)"** for Strategy D.
- README.md tagline: *"Cockpit for AI agents — Claude Agent, Codex, Ollama, OpenCode, Hermes, OpenClaw, and your own."*
- No ASCII art mimicking Claude Code's `*` welcome banner.

---

## H. Updated data model deltas

Add to V5 §5:

```python
# accounts/models.py
class Account(models.Model):
    # ... existing fields ...
    sdk_credit_quota: int | None     # for Anthropic accounts post-2026-06-15
    sdk_credit_used: int = 0
    sdk_credit_reset_at: datetime | None

# threads/models.py
class Thread(models.Model):
    # ... existing fields ...
    runtime_mode: str    # extended values: pty, rc, exec, api, sdk, observed, openclaw, hermes
    observed_jsonl_path: str | None  # for Strategy D — points at ~/.claude/projects/.../<sid>.jsonl

# telegram/models.py
class TelegramBinding(models.Model):
    id: UUID
    user: FK(User)
    telegram_user_id: BigIntegerField
    chat_id: BigIntegerField
    bot_token_ref: FK(Account)       # the bot account holding the encrypted token
    created_at, last_seen_at

class TelegramTopicMapping(models.Model):
    id: UUID
    thread: FK(Thread)
    telegram_topic_id: int
    binding: FK(TelegramBinding)
```

---

## I. Updated tasks (delta on V5 §8)

Add these tasks. Each carries the standard delegation packet.

### Phase 3.5 — Claude Agent SDK adapter (new, before Phase 3 §B.1)

**T-033 — Agent SDK Python wrapper (Strategy C)**
- Worker: **codex**
- Scope: `backend/apps/tier2/claude_agent_sdk.py` (lives in tier2 because it's HTTP-based to Anthropic, even though Agent SDK calls underlying Claude Code locally if installed); wraps `query()`, normalizes message types → NormalizedEvent, integrates hooks for command classification + audit
- Verification: integration test against Anthropic API; assert `SystemMessage init` yields session_id; assert resume works
- Acceptance: opus reviews because this is the primary Claude path

**T-034 — Agent SDK TypeScript fallback (Strategy C alt)**
- Worker: **codex**
- Scope: small Node.js side-process for users who prefer TS bundled Claude binary; backend spawns and talks via stdin/stdout JSON
- Verification: integration test
- Acceptance: feature parity with T-033

**T-035 — Agent SDK hooks → policy + audit bridge**
- Worker: **opus** (security)
- Scope: define `PreToolUse` hook that calls our command classifier; on High/Destructive risk, returns `{"decision": "block", "reason": ...}` blocking the tool; `PostToolUse` hook writes AuditEvent
- Verification: integration test: agent tries `rm -rf /`, hook blocks, audit captured
- Acceptance: opus mandatory — this is the enforcement boundary

**T-036 — Subscription quota awareness for Agent SDK accounts**
- Worker: **codex**
- Scope: track `sdk_credit_used` on Account; warn user at 80%, hard-stop at 100%; only applies to Anthropic accounts with `auth_type=subscription`; surface in /accounts page
- Verification: simulated quota fixture
- Acceptance: 100% quota cleanly stops, not silently fails

### Phase 3.6 — JSONL observed sessions (Strategy D)

**T-037 — Host daemon JSONL watcher**
- Worker: **codex**
- Scope: `host-agent/observers/claude_jsonl.py` — watchdog on `$CLAUDE_CONFIG_DIR/projects/` (default `~/.claude/projects/`); on new file → register Thread of mode=`observed`; tail JSONL, emit NormalizedEvent per line; track file rotation
- Verification: start `claude` outside cockpit, observe thread appearing in cockpit inbox within 5s
- Acceptance: handles JSONL with embedded newlines in strings; handles file deletion (30-day cleanup)

**T-038 — Inject message into observed session**
- Worker: **codex**
- Scope: on user reply in cockpit chat for an observed thread → spawn `claude --resume <sid> -p "<msg>"` headless, capture output, emit NormalizedEvent stream from that subprocess; do NOT use Agent SDK because the original session may have been started with subscription auth (no API key)
- Verification: end-to-end test
- Acceptance: respects original session's project directory; never spawns in wrong cwd

### Phase 4.5 — Telegram surface

**T-090 — Telegram bot app + auth**
- Worker: **codex**
- Scope: `apps/telegram/bot.py` + `auth.py` — Application from python-telegram-bot, /start handler creates TelegramBinding linked to a Django user via a deep-link token issued from the PWA
- Verification: send /start to bot → row appears
- Acceptance: bot token is age-encrypted in Account vault, never logged

**T-091 — Topic-per-thread mapping + new-thread flow**
- Worker: **codex**
- Scope: `handlers/new_thread.py` + `thread_mapper.py` — `/new claude-agent schatzi-api` creates a Topic in the user's DM (Bot API 9.4); maps Topic↔Thread bidirectionally
- Verification: integration test against a test bot in a sandbox group
- Acceptance: topic name updates on /rename

**T-092 — Message routing (text/voice/photo)**
- Worker: **codex**
- Scope: `handlers/messages.py` — text → thread; voice → faster-whisper → thread; photo → vision capability (only if model supports it, else reject with friendly error)
- Verification: send voice memo, assert transcription appears as user message
- Acceptance: faster-whisper model auto-downloads on first use

**T-093 — Approval inline keyboard**
- Worker: **codex**
- Scope: `handlers/approval.py` — when ApprovalRequest fires for a Telegram-bound user, push a message into the right Topic with inline ✅/❌ buttons; callback handler updates ApprovalRequest
- Verification: trigger High-risk command, approve from phone, assert approval recorded
- Acceptance: button latency < 2s end-to-end

**T-094 — Streaming response via editMessageText**
- Worker: **codex**
- Scope: stream Claude/Codex tokens into a single Telegram message by editing it in place every 500ms (rate-limit safe); on completion, finalize and move on
- Verification: visual: tokens appear incrementally
- Acceptance: respects Telegram rate limits (max 1 edit/sec per message)

**T-095 — Voice transcription module**
- Worker: **codex**
- Scope: `apps/telegram/transcribe.py` — faster-whisper init lazy-loaded; CPU fallback if no GPU/MPS; queue if multiple concurrent
- Verification: 10s voice file transcribed in < 5s on M2 MacBook
- Acceptance: handles silence + non-English

### Phase 4.6 — OpenCode adapter via open-remote-control

**T-100 — Detect open-remote-control plugin on host**
- Worker: **codex**
- Scope: probe extension — `curl http://localhost:<port>/health` against the plugin's default port; capture version; expose in host capabilities
- Verification: integration test on a Phil-host with the plugin installed
- Acceptance: graceful fallback to V5 Strategy B PTY if plugin absent

**T-101 — OpenCode adapter (HTTP+SSE client)**
- Worker: **codex**
- Scope: `host-agent/adapters/opencode_orc.py` — HTTP client to local open-remote-control server; thread start → POST /sessions; message → POST /sessions/{id}/messages; events → SSE stream `/sessions/{id}/events`
- Verification: integration test
- Acceptance: respects worktree boundary by passing project path

### Phase 9.5 — Branding + license compliance

**T-110 — Compliance pass: branding + licenses**
- Worker: **opus** (legal-adjacent)
- Scope: audit all README/docs/UI strings against Anthropic branding rule (§G); generate `LICENSES.md` enumerating each dependency + license; add `THIRD_PARTY_NOTICES.md` to the release artifact
- Verification: external license-checker tool (`pip-licenses` for Python, `license-checker` for npm) output matches `LICENSES.md`
- Acceptance: opus reviews

---

## J. Updated routing summary

```
~62 tasks total (V5 §8 had ~50; +12 here):
  ~26 → kimi
  ~25 → codex
  ~4 → ollama
  ~0 → sonnet
  ~7 → opus (added: T-035 hooks bridge, T-110 compliance)
```

Still ≥80% non-Opus. Opus footprint expanded only for security-critical bridge and license compliance.

---

## K. Updated competitive map

Add Telegram/messaging to V4 §2 matrix:

| Competitor | Tier 1 CLI | Tier 2 API | Mobile chat | Telegram | Multi-host | Multi-account | Audit |
|---|---|---|---|---|---|---|---|
| Remotelab | ✅ | ❌ | partial | ❌ | ❌ | ❌ | ❌ |
| open-remote-control (OpenCode) | partial (OpenCode only) | ❌ | partial | ✅ | ❌ | ❌ | partial |
| **OpenClaw** | ✅ (via integrations) | ✅ (model-agnostic) | n/a | ✅ | ❌ | partial | ❌ |
| **Hermes Agent** | partial | ✅ | n/a | ✅ | ❌ | ❌ | partial |
| ChatGOT / MultipleChat / Bind | ❌ | ✅ | ✅ web | ❌ | n/a | ❌ | ❌ |
| Claude Agent View | ✅ (Claude only) | n/a | ✅ (Anthropic surfaces) | ❌ | ❌ | ❌ | ❌ |
| **V5+addendum cockpit** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Even after OpenClaw's massive popularity, **no single product combines Tier 1 + Tier 2 + multi-host + multi-account + Telegram + PWA + policy/audit.** The cockpit's remaining moat is:

1. Multi-host (no competitor does this for code-agents).
2. Multi-account-per-provider with policy-bound thread→account binding.
3. Policy engine that runs *before* runtime start AND during tool calls (via Agent SDK hooks).
4. One unified audit log across heterogeneous runtimes.
5. Telegram + PWA + future native — pick your surface, same backend.

---

## L. Spec changelog when merged into V5

When this addendum is folded into V5:

1. Replace §0 row 3 (RC IPC) with: *"RC bridge: no IPC. But Claude DOES store session JSONL at `~/.claude/projects/<project>/<session-id>.jsonl` — watchable via filesystem events (Strategy D)."*
2. Replace §2 (two-strategy) with the four-strategy ranking in §B above.
3. Add §1.2 dependencies from §F.
4. Add Telegram app to §6 repo layout and §5 data model.
5. Add §G branding rule as a hard constraint above §1.1.
6. Renumber tasks to fold T-033..T-038, T-090..T-095, T-100..T-101, T-110 into V5 §8.
7. Update §9 routing tally to match §J.
8. Update §10 out-of-scope: remove "voice (ElevenLabs Phase 2)" — voice now arrives via Telegram + faster-whisper in MVP. ElevenLabs stays Phase 2 for outbound voice.

---

## M. Codex review still pending

A Codex review against the original V5 spec is in progress. When it returns:
- Capture verbatim into `09 Research/2026-05-25-codex-review-of-v5-spec.md`.
- Apply any blockers/highs immediately to V5.
- Note any findings that this addendum already addresses.
- Note any findings that this addendum invalidates (e.g. if codex flags PTY fragility, addendum's Strategy C demotion already covers it).

## Backlinks
- [[agent-command-center-v5-build-spec]]
- [[agent-command-center-v4-universal-cockpit]]
- [[Ideas — Project Overview]]
- [[Ideas - index]]
- [[2026-05-25-agent-command-center-market-review]]
