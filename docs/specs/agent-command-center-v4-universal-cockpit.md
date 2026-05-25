---
type: spec
project: Ideas
idea: Agent Command Center (universal cockpit reframe)
version: v4-universal-cockpit
date: 2026-05-25
status: draft — supersedes V3 if reframe is accepted
predecessor: agent-command-center-v3-sharpened
research: 2026-05-25-agent-command-center-market-review
---

# Agent Command Center V4 — Universal AI Chat Cockpit

> **Reframe.** V2 was "orchestrator for coding agents." V3 was "policy + approval layer on top of Remotelab." V4 is the product Philippe actually described: **a Beeper-style mobile-first cockpit where every AI chat window — coding CLIs, model APIs, voice agents, document AI, business agents — appears as a thread in one inbox, controllable with universal slash commands, with parallel sessions across multiple machines and accounts.**
>
> This is a different product. V3 was a thin governance overlay; V4 is a primary UI.

## 0. The vision in one paragraph

> *"Same simple text input as the Claude mobile app. But the dropdown above the input lets me pick any chat: Claude Code on my MacBook in repo X, Codex on my Windows machine in repo Y, Ollama Kimi running locally, a Claude API conversation with my Schatzi system prompt, an ElevenLabs voice agent, a Salesforce Agentforce thread, a Claude-for-Excel sheet. Each is a thread in my inbox. I can run dozens in parallel. I can type `/branch feature-x` in any coding thread, `/redact` in any thread, `/copy-to Codex` to fork a conversation to a different runtime. One audit trail. One credential vault. One push-notification stream for approvals. My phone is the cockpit."*

## 1. Hard reality — what is actually reachable

Before any design work, you must accept the 3-tier feasibility map:

### Tier 1 — Local CLIs (✅ fully reachable)

Reachable via PTY supervision on the host. Remotelab already does this pattern; we'd extend it.

- Claude Code
- Codex CLI
- OpenCode
- Aider
- Ollama (any model: Kimi K2, Gemma, Qwen, DeepSeek, GPT-OSS)
- Cline (CLI mode)
- Goose
- Kilo Code
- Custom shell scripts wrapped as agents

### Tier 2 — Provider APIs (✅ fully reachable, per-account auth)

Reachable via standard HTTPS with per-account credentials stored in a vault.

- Anthropic Messages API (Claude direct, multiple accounts)
- OpenAI Chat Completions / Responses API (ChatGPT, multiple accounts)
- Google Gemini API
- xAI Grok API
- OpenRouter (single key → many models)
- Mistral / DeepSeek / Together / Groq
- ElevenLabs Conversational AI API
- Salesforce Agentforce API (with org auth)
- ChatGPT Cowork / Claude Cowork APIs where exposed
- Custom OpenAPI-compatible endpoints

### Tier 3 — Closed third-party UIs (❌ effectively blocked)

These are Electron apps or SaaS UIs with no chat-injection API. They are actively designed to resist aggregation.

- **Cursor, Windsurf, Antigravity, Kiro** — Electron IDEs; chat panels are internal React state. No injection API. Workaround: launch them via CLI flags and hope they expose stdio (Cursor and Windsurf do not, as of May 2026). Realistic answer: **not reachable.**
- **Copilot in Excel / Word / PowerPoint / Outlook** — Microsoft 365 SaaS; chat UI inside Office canvases. Microsoft Agent 365 is the only sanctioned access and it's not a third-party aggregation target. Realistic answer: **not reachable.**
- **Claude for Office, Claude Cowork in M365 / Google Docs** — embedded provider widgets in third-party canvases. Same problem. **Not reachable.**
- **claude.ai web, chatgpt.com web, gemini.google.com web (consumer chat sites)** — require browser session + login + scraping. Brittle, ToS-violating. **Not reachable in a product we can ship.**
- **Salesforce Agentforce native UI (vs. its API)** — API is reachable (Tier 2); the UI inside Salesforce is not. Use the API.

**Verdict on Tier 3:** treat as out of scope. Any product that promises Tier 3 either does fragile browser-extension/accessibility hacks (Beeper-for-Messages-style problem — and Apple/Microsoft fight this constantly) or lies. The honest product covers Tier 1 + Tier 2 and explicitly tells users "the Cursor chat panel stays in Cursor; we don't aggregate it."

This leaves a real product. Tier 1 + Tier 2 covers the vast majority of *new* AI work; Tier 3 is mostly redundant chat surfaces over the same providers.

## 2. Competitive landscape for V4 (universal cockpit)

| Competitor | Tier 1 (CLIs) | Tier 2 (APIs) | Mobile-first chat UX | Multi-host | Parallel threads | Multi-account per provider | Universal slash commands | Audit |
|---|---|---|---|---|---|---|---|---|
| **Remotelab** | ✅ | ❌ | partial (mobile-friendly terminal) | ❌ | ✅ | ❌ | ❌ | ❌ |
| **agentsmesh** | ✅ | ❌ | ❌ | partial | ✅ | ❌ | ❌ | ❌ |
| **ChatGOT** | ❌ | ✅ | ✅ (web) | n/a | partial | ❌ | ❌ | ❌ |
| **MultipleChat** | ❌ | ✅ | partial | n/a | side-by-side compare | ❌ | ❌ | ❌ |
| **Bind AI** | partial | ✅ | partial | n/a | ✅ | ❌ | ❌ | ❌ |
| **AgentKits** | n/a — config file bridge only | n/a | n/a | n/a | n/a | n/a | ✅ (static rules) | n/a |
| **Claude Code Remote Control (official)** | ✅ (Claude only) | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Codex App (official)** | ✅ (Codex only) | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Gemini Enterprise** | ❌ | ✅ (Google only) | ✅ | n/a | ✅ | ❌ | partial | ✅ |
| **Microsoft Agent 365** | ❌ | ✅ (M365 only) | ✅ | n/a | ✅ | ❌ | partial | ✅ |
| **V4 cockpit (proposed)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**The empty row at the bottom is real and nobody fills it as of May 2026.** The intersection of (Tier 1 + Tier 2) × (mobile-first chat UX) × (multi-host) × (multi-account per provider) × (universal slash commands) × (audit) is the moat.

## 3. Product anatomy

### 3.1 The chat input (the UX everything orbits around)

```text
┌──────────────────────────────────────────────────────────┐
│  [▼ Thread picker]                              [+ New]  │
│  ──────────────────────────────────────────────────────  │
│                                                          │
│      [thread messages stream — same as Claude app]      │
│                                                          │
│  ──────────────────────────────────────────────────────  │
│  [text input ............................] [Send] [🎤]   │
└──────────────────────────────────────────────────────────┘
```

The thread picker is the entire product surface. Tapping it shows your inbox:

```text
ACTIVE
  🟢 Claude Code · Schatzi/api · macbook · feature/v3 · 12m ago
  🟢 Codex · Matisa/etl · windows-pc · branch-a · 3m ago
  🟢 Ollama Kimi · scratch · macbook · 1m ago
  🟡 Claude API · Schatzi sales prompt · 22m ago (awaiting approval)
  🟢 ElevenLabs voice · customer-demo · just now

PINNED
  📌 Claude API · "Schatzi onboarding assistant" (persistent system prompt)
  📌 Codex · Matisa weekly digest (recurring)

RECENT
  ⚪ Aider · Schatzi/ui · macbook · done 2h ago — diff ready
  ⚪ Claude Code · OfficeLabs/specs · vps · stopped 6h ago

[+ New thread]    [⚙ Hosts]    [🔑 Accounts]    [📜 Audit]
```

Mobile-first. Same input box drives any thread. Long-press a thread → fork / duplicate to another runtime / archive.

### 3.2 New-thread flow

```text
New thread:
  Runtime:       [Claude Code ▼]   (Tier 1 list + Tier 2 list)
  Host:          [macbook ▼]       (only shown if runtime is Tier 1)
  Account:       [Anthropic — phil personal ▼]
  Project:       [Schatzi/api ▼]   (only shown if runtime is project-aware)
  System prompt: [optional, from library]
  Policy:        [auto — Schatzi default (confidential)]
```

Hit start → backend opens the session (via PTY on the host, or via API call to the provider, or via Remotelab passthrough).

### 3.3 Universal slash commands (`/command` works in any thread)

These are middleware commands the cockpit intercepts **before** sending text to the underlying runtime. They are not provider-specific — they are cockpit-level verbs.

| Command | Effect | Works in |
|---|---|---|
| `/fork <runtime>` | Duplicate this thread's context into a new thread on a different runtime | any |
| `/copy-context-to <thread>` | Append this thread's last N messages into another thread | any |
| `/host <name>` | Move this session to a different machine (Tier 1 only) | Tier 1 |
| `/branch <name>` | Create a git branch + worktree for this session | Tier 1 coding |
| `/diff` | Show the diff for this session's worktree | Tier 1 coding |
| `/approve` | Approve the pending approval in this session | any |
| `/reject` | Reject pending approval | any |
| `/redact` | Mark this message for redaction in audit log | any |
| `/save-as-skill <name>` | Save current system prompt + last context as a reusable thread template | any |
| `/run-skill <name>` | Apply a saved skill to current thread | any |
| `/pin` | Pin thread to top | any |
| `/handoff <thread>` | Pass this thread's task as a message into another thread | any |
| `/account <name>` | Switch the credential for this thread | Tier 2 |
| `/model <id>` | Switch the model for this thread | Tier 2 |
| `/stop` | Graceful stop of underlying runtime | any |

These are intercepted by the cockpit and never reach the underlying runtime as text. **This is the universal `/command remote-control` the user described.**

### 3.4 Multi-account per provider

A first-class concept. The Accounts vault stores per-provider credentials with labels:

```text
Anthropic
  - phil personal (Max plan)              [OAuth token, expires 2026-08]
  - Schatzi org (Team plan)               [API key]
  - OfficeLabs (Team plan)                [API key]

OpenAI
  - phil personal (ChatGPT subscription)  [OAuth for Codex]
  - Matisa client account                 [API key]

Google
  - phil@gmail (Gemini personal)          [OAuth]
  - schatzi-ai workspace                  [service account]

Ollama
  - localhost                             [no auth]
  - VPS                                   [bearer token]

ElevenLabs
  - phil personal                         [API key]

Salesforce
  - Schatzi org sandbox                   [OAuth]
```

Any thread is created against exactly one account. The cockpit prevents leakage: a thread on the Schatzi account cannot read context from a thread on a personal account unless explicitly forwarded with `/copy-context-to`.

### 3.5 Multi-host

Identical to V3. Each host runs a small daemon that registers itself, reports installed Tier 1 runtimes, and accepts session-start commands over Tailscale.

### 3.6 Parallel sessions

No fundamental limit — each thread is just a process (Tier 1) or HTTP connection (Tier 2). Cockpit shows live count per host and per account, with policy caps (max parallel Claude Code sessions per host, max parallel API calls per account per minute).

### 3.7 Audit + approvals

Inherited unchanged from V3. Every send, every receive, every tool call, every approval — one append-only Postgres table, queryable, redactable.

## 4. Architecture

```text
┌──────────────────────────────────────────────────────────┐
│  Mobile PWA (iOS/Android home-screen install)             │
│  — chat-first UI, thread inbox, slash-command middleware  │
│  — push notifications for approvals                       │
└────────────────────┬─────────────────────────────────────┘
                     │ HTTPS over Tailscale Serve
                     ▼
┌──────────────────────────────────────────────────────────┐
│  Cockpit backend  (Django + Channels + Postgres + Redis)  │
│  ─ Thread router (Tier 1 vs Tier 2 dispatch)              │
│  ─ Slash command middleware                               │
│  ─ Account vault (encrypted creds, per-thread binding)    │
│  ─ Policy engine (sensitivity + risk tiers)               │
│  ─ Approval queue                                          │
│  ─ Audit log (append-only Postgres)                       │
│  ─ Skill library (reusable thread templates)               │
└──────┬───────────────────────────┬───────────────────────┘
       │                           │
       │ Tailscale                 │ HTTPS (per-account)
       ▼                           ▼
┌──────────────┐         ┌──────────────────────────────┐
│ Host daemons │         │ Provider APIs                 │
│ on each Mac/ │         │  Anthropic / OpenAI / Google  │
│ PC/VPS       │         │  Mistral / OpenRouter / xAI   │
│              │         │  ElevenLabs / Salesforce      │
│ PTY-supervise│         │  Ollama / custom OpenAPI      │
│ Tier 1 CLIs  │         └──────────────────────────────┘
│ (Claude Code,│
│  Codex,      │
│  Ollama, …)  │
└──────────────┘
```

## 5. Build vs. wrap vs. integrate

| Concern | Decision | Reason |
|---|---|---|
| PTY supervision of local CLIs (Tier 1) | **Wrap Remotelab if its API is wide enough; else fork** | Remotelab already covers this. Don't rebuild. Spike Week 0. |
| Provider API client per provider (Tier 2) | **Build thin per-provider adapters** | Each provider is a few hundred lines. No good universal lib that covers chat + tool-use + streaming + multi-account well. |
| Mobile-first chat UI | **Build** | Real differentiation. Use Next.js PWA + Tailwind + a chat-message lib (rsc / Vercel AI SDK UI primitives). |
| Slash command middleware | **Build** | Real differentiation. Tiny piece of code, huge UX value. |
| Account vault | **Build on top of Postgres + age/sops encryption** | Don't build a new HSM. Encrypt at rest, decrypt in-memory per request. |
| Multi-host registry | **Build** (from V3) | Real differentiation. |
| Policy engine + audit | **Build** (from V3) | Real differentiation. |
| Push notifications | **Use Pushbullet / NTFY / web-push** | Don't build notification infra. |
| Tier 3 (Cursor, Office Copilot) | **Don't build** | Vendor-blocked. Explicitly tell users this. |
| LLM gateway | **Don't build** | Each Tier 2 adapter calls the provider directly with the bound account. |
| Custom agent runtime | **Don't build** | Use the providers and CLIs that exist. |

## 6. MVP scope — 6 weeks, 1 person

Tighter than V3 because the moat is now the chat UX, not policy.

### Week 0 — Remotelab spike
- Can we drive Remotelab from Python with stable APIs for: start session, send message, stream events, stop? If yes → wrap. If no → write a 200-line PTY supervisor ourselves.
- **Exit:** decision documented, foundation chosen.

### Week 1 — backend skeleton + accounts + hosts
- Django + Postgres + Channels
- `Account`, `Host`, `Project`, `PolicyProfile`, `Thread`, `Message`, `AuditEvent`, `ApprovalRequest` models
- Account vault with age/sops encryption
- Host daemon (cron heartbeat, runtime probe)
- **Exit:** seed 3 hosts, 5 accounts, 3 projects via admin.

### Week 2 — Tier 1 adapter (Claude Code + Codex + Ollama)
- Wrap Remotelab (or PTY supervisor) for Claude Code, Codex, Ollama
- Stream events into `Message` and `AuditEvent`
- Simple web view: thread list, open thread, send message
- **Exit:** start a Claude Code thread on MacBook from the web, type, get streaming response.

### Week 3 — Tier 2 adapter (Anthropic + OpenAI + Ollama + ElevenLabs)
- 4 provider adapters (chat + streaming + tool-use where applicable)
- Per-thread account binding
- Same web view now handles Tier 2 threads
- **Exit:** create thread bound to Schatzi Anthropic account → send message → streaming response. Switch to Schatzi OpenAI account → same thread compatibility (or `/fork`).

### Week 4 — mobile PWA + slash command middleware
- Next.js PWA, mobile-first chat UI (steal patterns from Claude app)
- Thread inbox + new-thread flow
- Slash middleware: `/fork`, `/branch`, `/diff`, `/approve`, `/reject`, `/stop`, `/host`, `/account`, `/model`
- Push notifications via NTFY
- **Exit:** install PWA on phone over Tailscale; run a coding thread and an API thread in parallel from the phone.

### Week 5 — policy + approval + audit
- PolicyProfile → sensitivity-based account/runtime restrictions
- ApprovalRequest on High/Destructive Tier 1 commands + on push to confidential repos
- Audit view in admin (queryable)
- Secrets redaction at ingest
- **Exit:** confidential Schatzi thread cannot bind to a cloud account; push to confidential repo blocks until phone approval.

### Week 6 — skill library + hardening
- `/save-as-skill` and `/run-skill` (reusable system-prompt + project + account triples)
- Backup (Postgres dump → encrypted S3-compatible)
- Tailscale ACL hardening
- Dogfood week — actually use the cockpit for all AI work for 5 days
- **Exit:** if you can drop the Claude mobile app + Codex app + 4 other tabs in favor of the cockpit, ship V1. If not, write postmortem and decide kill vs. iterate.

## 7. Kill criteria (Week 2 + Week 4 checkpoints)

Week 2: if PTY supervision + streaming has > 1s latency or drops events, kill — chat UX is dead without snappy streaming.

Week 4: if you don't naturally reach for the PWA over native Claude/Codex apps on Day 3 of dogfooding, kill — the moat is UX, and if your own UX loses to the providers' mobile apps, no one else will switch either.

## 8. What V4 explicitly does not do

- **No Cursor / Windsurf / Antigravity chat aggregation** — vendor-blocked.
- **No Office Copilot / Claude for Office aggregation** — vendor-blocked.
- **No consumer claude.ai / chatgpt.com aggregation** — only the APIs of those providers, not the web chat sessions.
- **No team/multi-user features** — explicit non-goal for V1. Single human.
- **No marketplace, no plugin SDK** — V2 features.
- **No agent-builder / no LangGraph orchestration** — those are agents to be hosted *as* threads, not built inside the cockpit.

## 9. Open questions

1. **Voice in / voice out** — desirable on mobile. ElevenLabs Conversational AI handles voice-to-voice; would the cockpit add a `🎤 → voice` mode for any thread, transcribing via Whisper API and speaking responses via ElevenLabs? Adds ~3 days.
2. **Inline approvals via push** — can we make iOS push notifications carry "Approve / Reject" actions natively (UNNotificationAction)? Yes for native iOS, partial for web push. Decide whether to ship native iOS shell or stay PWA.
3. **Salesforce / SAP / OfficeLabs business agents** — Tier 2 reachable but each needs custom OAuth and per-org auth flows. Defer to V2 unless a specific need exists.
4. **Local-only mode** — can the cockpit run entirely on the MacBook with no VPS, dashboard accessed only when phone is on same Tailscale? Yes. Recommended for sovereignty-first deployment.

## 10. Decision required from Philippe

V3 and V4 are different products. Pick one:

- **V3 (governance overlay)** — 4 weeks. Smaller scope. Honest defensible niche. Boring product.
- **V4 (universal cockpit)** — 6 weeks. Bigger ambition. Replaces the native Claude/Codex mobile apps for you personally if it works. Real differentiation. Higher risk: chat UX must be excellent.
- **A — Abandon both.** Use Remotelab + native Claude/Codex apps + ChatGOT/Bind for API chat + manual tab-switching. Re-evaluate Q4 2026.
- **C — Pivot to OfficeLabs document-agent cockpit.** Same V4 architecture, business-document vertical, no direct competitor.

Default if no answer in 1 week: **A** (the market is moving too fast to build speculatively).

**Recommendation:** V4 is the product Philippe actually wants. V3 is the safer build. If you have 6 weeks of focus and you'd dogfood the result every day, build V4. If not, default A.

## Backlinks
- [[Ideas — Project Overview]]
- [[Ideas - index]]
- [[agent-command-center-v3-sharpened]]
- [[agent-command-center-v2-archived]]
- [[2026-05-25-agent-command-center-market-review]]
