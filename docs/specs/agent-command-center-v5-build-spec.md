---
type: spec
project: Ideas
idea: Agent Command Center
version: v5-build-spec
date: 2026-05-25
status: draft — pending Codex review
predecessor: agent-command-center-v4-universal-cockpit
research: 2026-05-25-agent-command-center-market-review
delegation-style: opus-supervises-cheaper-workers
license: planned MIT or Apache-2.0
---

# Agent Command Center V5 — Open-Source Build Spec

> **Purpose of this document.** A buildable, end-to-end specification that (a) corrects assumptions from V2–V4 against the official Claude Code Remote Control docs, (b) chooses 100% free / open-source / self-hostable components, (c) decomposes the work into ~50 discrete tasks each tagged with the cheapest worker tier capable of executing it (kimi `/execute-task`, codex `/codex-worker`, ollama `/ollama-agent`, sonnet, opus). Opus only writes the spec, reviews diffs, signs off commits. The implementation itself is delegated.
>
> Codex must review this spec before any code is written. Review prompt in §11.

---

## 0. Reality check — what V2/V3/V4 got wrong about Claude Code Remote Control

After reading the [official docs](https://code.claude.com/docs/en/remote-control) on 2026-05-25:

| Earlier assumption | Corrected reality |
|---|---|
| "RC only on Pro/Max" | **All plans** (Pro/Max/Team/Enterprise). Team/Enterprise admin must enable the toggle. |
| "We can wrap Claude RC" | **We cannot.** RC connects Claude Code to *claude.ai/code or the Claude mobile app*. The bridge is Anthropic-owned and there is no MITM surface. We can only run RC sessions ourselves and **register the session URL** in our cockpit. |
| "RC exposes IPC we can read" | **No.** Outbound HTTPS only. No inbound ports. No documented local files for session state. |
| "We can use the OAuth token from `claude setup-token`" | **Rejected.** Long-lived `CLAUDE_CODE_OAUTH_TOKEN` is inference-only and refused by RC. Only `claude auth login` interactive OAuth works. |
| "We need to build worktree isolation" | **Already built into RC.** `claude remote-control --spawn worktree` gives each session its own git worktree. Press `w` at runtime to toggle. |
| "We need to build session capacity" | **Already built.** `--capacity N` (default 32). |
| "We need to build sandboxing" | **Already built.** `--sandbox` / `--no-sandbox`. Off by default. |
| "We need to invent push notifications" | **Already built.** Claude app sends push when Claude decides ("notify me when tests finish"). Single on/off toggle in `/config`. v2.1.110+. |
| "RC is the only mobile path" | **No.** Anthropic ships 5 parallel surfaces: Dispatch, Remote Control, Channels, Slack, Scheduled tasks. Each has a different trigger model. Our cockpit competes with all of them, or complements via Channels. |

**Implication for V5.** The Claude Code adapter is much smaller than V2–V4 assumed. We don't aggregate the RC bridge — we register and track RC sessions as one of multiple thread types. The interesting integration is via **Channels** (we can BE a channel that forwards arbitrary events into a Claude Code session).

---

## 1. Open-source stack — every component free and self-hostable

### 1.1 Hard rule: zero proprietary services in the critical path

A user must be able to clone, `make install`, and run the entire stack on a single MacBook with no paid services other than the LLM provider API keys they bring themselves.

### 1.2 Stack table

| Layer | Choice | License | Why |
|---|---|---|---|
| Backend framework | **Django 5.1** + **Django REST Framework** + **Django Channels** | BSD / Apache-2.0 | Mature, batteries included, Postgres-first, real WebSocket support. Avoid FastAPI: we want admin + ORM + auth + migrations for free. |
| Database | **PostgreSQL 16** | PostgreSQL License | Foreign keys, JSONB for event payloads, partial indexes for active threads, partitioning for audit. |
| Queue / pubsub | **Redis 7** (single binary) | RSAL/SSPL (or **Valkey** if we need fully OSI) | Channels backend + Celery broker + thread fan-out. **Decision: prefer Valkey 8 over Redis post-2024 license change.** |
| Background jobs | **Celery 5** | BSD | Long-running PTY supervision, scheduled probes, retention. |
| Frontend framework | **Next.js 15 (App Router)** + **React 19** | MIT | PWA-friendly, server components, mobile-first viable. |
| Styling | **Tailwind CSS 4** + **shadcn/ui** | MIT | Composable, mobile-first, no design lock-in. |
| Chat UI primitives | **Vercel AI SDK UI** (`@ai-sdk/react`) | Apache-2.0 | Streaming text, tool-call rendering, message lists. Avoid building from scratch. |
| Mobile install | **PWA** via Next.js + `next-pwa` | MIT | iOS Add-to-Home installs work. Skip native unless push-action UNNotificationAction proves necessary. |
| Push notifications | **NTFY** (self-hosted or ntfy.sh) | Apache-2.0 | Free, simple POST → push. Web Push fallback. Skip APNs/FCM (cost + complexity). |
| Host daemon language | **Python 3.13** (asyncio) | PSF | Same lang as backend, ptyprocess + ptyprocess works on macOS/Linux, **pywinpty** for Windows. |
| PTY library | **ptyprocess** (Unix) + **pywinpty** (Windows) | ISC / MIT | Battle-tested. |
| Git operations | **GitPython** + raw `git` CLI fallback | BSD | Worktree, branch, diff — GitPython is fine but shell out for worktree commands (more reliable). |
| Secrets at rest | **age** (CLI + `pyrage` Python binding) | MIT | Modern, simple, no GPG pain. Per-account encrypted at rest, decrypted in-memory. |
| Connectivity | **Tailscale** (free tier: 100 devices, 3 users) | BSD core, proprietary control plane | Acceptable: control plane is free for our scale, data plane is FOSS WireGuard. Alternative: pure WireGuard for sovereignty diehards. |
| Reverse proxy | **Caddy 2** | Apache-2.0 | Auto-TLS, simple config, plays well with Tailscale Serve. |
| Observability | **OpenTelemetry** SDKs → **Grafana Loki** (logs) + **Grafana Tempo** (traces) + **Prometheus** (metrics) + **Grafana** dashboards | Apache-2.0 / AGPL | All FOSS, self-hostable in one docker-compose. |
| LLM telemetry (optional) | **Langfuse** self-hosted | MIT (core) | Captures LLM prompts/responses/cost for Tier 2 threads. Skippable. |
| Eval / regression | **promptfoo** | MIT | CLI evals for adapter response shapes. |
| Container / dev | **Docker Compose** + **devcontainer.json** | Apache-2.0 / MIT | One-command dev environment. |
| CI | **GitHub Actions** (free for public repos) | proprietary, free | Acceptable since repo will be OSS. Mirror to **Forgejo Actions** for self-hosted option. |
| Docs | **Docusaurus 3** | MIT | Static site, sane defaults. |
| License | **Apache-2.0** for code, **CC-BY-SA 4.0** for docs | OSI / CC | Apache-2.0 over MIT for patent grant; matters if this gets adopted. |
| Codex of conduct | **Contributor Covenant 2.1** | CC-BY 4.0 | Standard. |

### 1.3 Explicitly avoided

- ❌ **Auth0 / Clerk / Supabase Auth** — Django auth is enough for single-user; add `django-allauth` when multi-user lands.
- ❌ **Vercel / Railway / Fly hosted** — must run on a laptop.
- ❌ **Pinecone / Weaviate** — no vector search in V1; if needed later, **pgvector**.
- ❌ **OpenAI Assistants API** — non-portable. Tier 2 adapters use plain Chat Completions / Messages API.
- ❌ **Tailscale Funnel** — public exposure not in scope.
- ❌ **Slack / Discord webhooks for primary UX** — they are integration targets, not the UI.

---

## 2. Claude Code adapter — two strategies, both implemented

### 2.1 Strategy A — RC-link adapter (lightweight, official path)

**Mechanism.** Host daemon runs:
```bash
claude remote-control \
  --name "{thread.name}" \
  --spawn worktree \
  --capacity 1 \
  --sandbox \
  --remote-control-session-name-prefix "acc-{host.slug}"
```
Daemon parses stdout for the session URL (regex on the URL line printed by `claude remote-control`). Stores the URL on `Thread.external_session_ref`. Cockpit deep-links the user into claude.ai/code or the Claude app for live interaction. We capture: session start/stop, worktree path, any git diff after the process exits.

**What we get.** Real Claude Code with full RC features (push, mobile, sync). Zero re-implementation. Worktree isolation native. Sandbox flag enforced.

**What we lose.** No live message stream into our cockpit chat UI for these threads. The thread shows as "click here to open in Claude" with status + diff + audit metadata, not in-app chat.

**Constraints to honor.**
- Process must keep running (daemon supervises restart on crash, NOT on >10 min network outage — RC terminates by design).
- One RC session per `claude` process unless using server mode. We always use server mode with `--capacity 1` to keep policy boundary clean (one thread = one process = one OS account scope).
- `CLAUDE_CODE_OAUTH_TOKEN` env var must NOT be set in the daemon's environment.
- Detect plan eligibility via `claude /status` parse on first probe; refuse to create thread if `Remote Control is not yet enabled for your account`.

### 2.2 Strategy B — PTY supervisor (in-cockpit chat)

**Mechanism.** Host daemon launches plain `claude` (no `--remote-control` flag) inside a PTY, streams stdout to backend over WebSocket, accepts user messages and writes them to the PTY's stdin. Thread appears as a native chat in the cockpit inbox.

**What we get.** Native cockpit chat UX. Universal slash middleware applies (`/branch`, `/diff`, `/approve`, `/fork`, etc.). Auditable message stream.

**What we lose.** No RC features (no mobile push from Claude, no claude.ai/code sync). Stdout parsing fragile if Claude Code formatting changes.

**Constraints to honor.**
- Use `--print` mode for one-shot prompts (clean output) when possible.
- For interactive multi-turn: terminal sequences (ANSI) must be stripped; use a library like `pyte` to handle the terminal emulator state.
- Reject in-cockpit threads on Tier 3 features that don't work in interactive PTY mode: `/mcp`, `/plugin`, `/resume`.

### 2.3 Why both

Each user picks per-thread. Defaults:
- **Sensitive project + needs phone**: A (RC-link) — pushes happen via official Anthropic infra, audit captures session metadata + diff only.
- **Long parallel coding sessions watched from cockpit**: B (PTY) — chat in-app, no provider notification, full message audit.

### 2.4 Channels integration (bonus, Phase 2)

Implement an outbound channel client conforming to Anthropic's [Channels protocol](https://code.claude.com/docs/en/channels-reference). The cockpit becomes a channel: events generated by the cockpit (an approval, a deploy result, a Schatzi event) get forwarded into a designated Claude Code session as messages. Reverse direction: not provided by Channels (channels are inbound to Claude Code only).

---

## 3. Codex adapter

### 3.1 What we can rely on (May 2026)

Per Codex CLI changelog and the V2 spec's earlier inspection:

- `codex --version` — version detection
- `codex exec` — non-interactive one-shot mode with `-s read-only|workspace-write|danger-full-access`, `-m <model>`, `--ephemeral` flag for no session persistence
- `codex` (interactive) — TUI, harder to PTY-supervise cleanly
- `codex remote-control` — direction confirmed but command surface unstable; **feature-detect**
- Permission profiles via `codex` config files

### 3.2 Strategy

- **Tier 1 default**: `codex exec --ephemeral -s workspace-write -m gpt-5.5 "<task>"` invoked per cockpit message. Captures stdout, parses for diff. Cheap, predictable, no PTY needed.
- **Long-running**: PTY-supervise `codex` interactive when user explicitly opts in. Same constraints as Claude PTY strategy.
- **Remote control**: register adapter capability flag `supports_remote_control`. If `codex remote-control --help` exits 0 in probe, expose the link-out flow analogously to Claude Strategy A.

### 3.3 ChatGPT subscription auth

Codex authenticates via the ChatGPT subscription on the host (not API key). Daemon must NOT set `OPENAI_API_KEY` env var — would override. Confirm via `codex login status` in probe.

---

## 4. Other adapters

| Runtime | Tier | Strategy | Notes |
|---|---|---|---|
| **Ollama** | 1 + 2 | Local HTTP API `/api/chat` streaming JSONL | Native streaming, no PTY needed. Multi-account = multi-host (one Ollama per host). |
| **OpenCode** | 1 | PTY | Treat like Claude Code Strategy B. |
| **Aider** | 1 | `aider --message "..." --yes` non-interactive per message | Captures diff via aider's own diff output. |
| **Anthropic API** | 2 | `anthropic` Python SDK with per-account key | Streaming via SSE. |
| **OpenAI API** | 2 | `openai` Python SDK with per-account key | Streaming via SSE. |
| **Gemini API** | 2 | `google-genai` SDK | Streaming. |
| **OpenRouter** | 2 | OpenAI-compatible SDK pointed at `openrouter.ai/api/v1` | Universal fallback. |
| **ElevenLabs Conversational AI** | 2 | `elevenlabs` SDK; WebSocket protocol | Voice-only, special UI. Phase 2. |
| **Salesforce Agentforce** | 2 | REST API with per-org OAuth | Phase 3. |

---

## 5. Final data model (frozen for V5 MVP)

```python
# accounts/models.py
class Account(models.Model):
    id: UUID
    provider: str           # anthropic, openai, google, ollama, elevenlabs, salesforce
    label: str              # "phil personal" / "Schatzi org"
    auth_type: str          # oauth, api_key, none
    encrypted_credential: bytes  # age-encrypted blob
    metadata: JSONB         # expiry, scopes, org_id, etc.
    created_at, updated_at

# hosts/models.py
class Host(models.Model):
    id: UUID
    slug: str               # unique
    name: str
    os: str                 # darwin, win32, linux
    tailscale_dns: str
    last_seen_at: datetime
    status: str             # online, offline, degraded
    capabilities: JSONB     # installed runtimes + versions + flags

# projects/models.py
class Project(models.Model):
    id: UUID
    slug: str
    name: str
    repo_url: str | None
    sensitivity: str        # public, internal, confidential, regulated
    policy: FK(PolicyProfile)
    local_paths: JSONB      # {host_slug: absolute_path}
    allowed_accounts: M2M(Account)
    allowed_hosts: M2M(Host)
    allowed_runtimes: JSONB # [{runtime: claude_code, mode: pty|rc}]

class PolicyProfile(models.Model):
    id: UUID
    name: str
    sensitivity_max: str    # max sensitivity this profile permits
    allow_cloud_models: bool
    require_worktree: bool
    require_approval_for: JSONB  # ["push", "open_pr", "install", "network", "deploy"]
    block_destructive: bool
    max_runtime_minutes: int
    max_parallel_threads: int

# threads/models.py
class Thread(models.Model):
    id: UUID
    name: str
    runtime: str            # claude_code, codex, ollama, aider, opencode, anthropic_api, openai_api, ...
    runtime_mode: str       # pty, rc, exec, api
    host: FK(Host, nullable=True)         # null for Tier 2
    account: FK(Account)
    project: FK(Project, nullable=True)
    status: str             # pending, starting, running, waiting_approval, completed, failed, stopped
    external_session_ref: str | None      # RC session URL, or upstream session id
    worktree_path: str | None
    branch_name: str | None
    started_at, last_event_at, ended_at
    metadata: JSONB

class Message(models.Model):
    id: UUID
    thread: FK(Thread)
    role: str               # user, assistant, system, tool, slash, system_event
    content: TEXT
    redacted_content: TEXT | None
    sequence: int
    created_at: datetime
    metadata: JSONB         # tool_call_id, model, tokens, latency_ms

# audit/models.py
class AuditEvent(models.Model):
    id: BigAutoField        # partitioned by month
    timestamp: datetime
    thread: FK(Thread, nullable=True)
    actor: str              # user_id, system, runtime
    event_type: str         # thread_create, message_send, approval_request, approval_grant,
                            # policy_block, command_classify, runtime_start, runtime_stop, redaction
    payload: JSONB
    redacted_payload: JSONB | None

class ApprovalRequest(models.Model):
    id: UUID
    thread: FK(Thread)
    request_type: str       # run_command, push_branch, open_pr, install_package, network, deploy, cross_account_fork
    risk: str               # low, medium, high, destructive
    summary: str
    preview: TEXT
    status: str             # pending, approved, rejected, expired
    requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    expires_at: datetime

# skills/models.py
class Skill(models.Model):
    id: UUID
    name: str
    description: str
    system_prompt: TEXT
    default_runtime: str
    default_account: FK(Account, nullable=True)
    default_project: FK(Project, nullable=True)
    metadata: JSONB
```

Migrations: one per app, generated. Audit partitioned via `django-postgres-extra` or raw SQL.

---

## 6. Repository layout

```
agent-command-center/
├── LICENSE                                  # Apache-2.0
├── README.md                                # quickstart + screenshots
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md                              # security disclosure policy
├── CHANGELOG.md
├── .github/
│   ├── workflows/{ci.yml, release.yml, codeql.yml}
│   └── ISSUE_TEMPLATE/, PULL_REQUEST_TEMPLATE.md
├── docker-compose.yml                       # one-command dev
├── docker-compose.prod.yml                  # single-host prod
├── Makefile                                 # make install/dev/test/lint/fmt
├── docs/                                    # docusaurus
├── backend/                                 # Django
│   ├── manage.py
│   ├── pyproject.toml                       # uv-managed
│   ├── config/                              # settings, urls, asgi, celery
│   ├── apps/
│   │   ├── accounts/      models + vault + admin + serializers + views
│   │   ├── hosts/         registry, heartbeat, probe results
│   │   ├── projects/
│   │   ├── policies/
│   │   ├── threads/       Thread, Message + WebSocket consumer
│   │   ├── adapters/      router (NOT individual adapters; those live on host daemon for Tier 1)
│   │   ├── tier2/         Tier 2 provider adapters (run in backend, not host)
│   │   ├── approvals/
│   │   ├── audit/
│   │   ├── skills/
│   │   └── slash/         slash command middleware
│   └── tests/
├── host-agent/                              # Python daemon, one binary per host
│   ├── pyproject.toml
│   ├── agent_host/
│   │   ├── __main__.py
│   │   ├── config.py
│   │   ├── transport.py                     # WebSocket to backend
│   │   ├── heartbeat.py
│   │   ├── runtime_probe/
│   │   │   ├── claude_code.py               # `claude --version`, `/status`, capability flags
│   │   │   ├── codex.py
│   │   │   ├── ollama.py
│   │   │   ├── opencode.py
│   │   │   └── aider.py
│   │   ├── adapters/                        # Tier 1 runtime adapters
│   │   │   ├── base.py                      # abstract interface (§7.2)
│   │   │   ├── claude_code_pty.py           # Strategy B
│   │   │   ├── claude_code_rc.py            # Strategy A
│   │   │   ├── codex_exec.py
│   │   │   ├── codex_pty.py
│   │   │   ├── ollama_http.py
│   │   │   ├── opencode_pty.py
│   │   │   └── aider_exec.py
│   │   ├── pty/
│   │   │   ├── unix_runner.py               # ptyprocess
│   │   │   ├── windows_runner.py            # pywinpty
│   │   │   └── ansi_strip.py                # pyte-based terminal emulator
│   │   ├── git/
│   │   │   ├── worktree.py
│   │   │   ├── branch.py
│   │   │   └── diff.py
│   │   ├── policy_client/                   # talks to backend policy engine before destructive acts
│   │   └── secrets/
│   │       └── redactor.py                  # regex + entropy + .env key names
│   └── tests/
├── frontend/                                # Next.js PWA
│   ├── package.json
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                         # thread inbox
│   │   ├── thread/[id]/page.tsx             # chat view
│   │   ├── approvals/page.tsx
│   │   ├── hosts/page.tsx
│   │   ├── accounts/page.tsx
│   │   ├── audit/page.tsx
│   │   └── skills/page.tsx
│   ├── components/
│   │   ├── chat/{message-list, composer, slash-menu}.tsx
│   │   ├── thread/{thread-card, thread-picker}.tsx
│   │   ├── approval/{card, action-buttons}.tsx
│   │   └── host/{status-pill, runtime-badges}.tsx
│   ├── lib/
│   │   ├── api.ts                           # typed REST client
│   │   ├── ws.ts                            # WebSocket hooks
│   │   └── slash.ts                         # slash command parser (mirrors backend)
│   └── public/manifest.json, sw.js
├── deploy/
│   ├── tailscale/                           # ACL examples
│   ├── caddy/Caddyfile.example
│   ├── ntfy/server.yml.example
│   └── observability/                       # docker-compose for loki/tempo/prom/grafana
└── examples/
    ├── policy-profiles/{public, internal, confidential.yaml}
    ├── skills/                              # sample skill templates
    └── projects/seed.yaml
```

---

## 7. Adapter contract (host daemon side)

### 7.1 Why this lives on the host

Tier 1 adapters (PTY-based) MUST run on the host where the CLI is installed. They can't be backend-side. Tier 2 adapters (HTTP-based) run on the backend, where they get encrypted credentials from the vault.

### 7.2 Abstract base (Python, `host-agent/adapters/base.py`)

```python
from typing import AsyncIterator, Protocol
from dataclasses import dataclass

@dataclass
class ProbeResult:
    runtime: str
    installed: bool
    version: str | None
    capabilities: dict      # {supports_rc: bool, supports_sandbox: bool, supports_worktree: bool, auth_ok: bool}
    error: str | None

@dataclass
class StartRequest:
    thread_id: str
    project_path: str | None
    branch: str | None
    worktree_path: str | None
    sandbox: bool
    extra: dict             # adapter-specific (e.g. claude_code rc-mode flag)

@dataclass
class NormalizedEvent:
    seq: int
    kind: str               # stdout, stderr, model_message, tool_call, tool_result, command, diff, approval_needed, exit
    payload: dict

class RuntimeAdapter(Protocol):
    runtime: str

    async def probe(self) -> ProbeResult: ...
    async def start(self, req: StartRequest) -> str:  # returns session handle
        ...
    async def send_message(self, handle: str, text: str) -> None: ...
    async def stream_events(self, handle: str) -> AsyncIterator[NormalizedEvent]: ...
    async def stop(self, handle: str, force: bool = False) -> None: ...
    async def collect_artifacts(self, handle: str) -> list[dict]: ...
```

Every Tier 1 adapter is < 400 lines including tests. This is intentionally small so a kimi worker can write one in 2–3 iterations.

---

## 8. Delegation-friendly task breakdown

Every task carries a **delegation packet** in the format from `~/.claude/rules/model-routing.md`. Opus reviews each task's PR before merge. Tasks are ordered as a DAG; deps are noted.

### Legend

- `kimi` = `/execute-task` (free, Ollama subscription, mechanical edits / multi-file refactors with clear spec)
- `codex` = `/codex-worker` (free, ChatGPT subscription, code quality / tests / narrow refactor)
- `ollama` = `/ollama-agent` local gemma4:31b (free, one-shot text)
- `sonnet` = Sonnet Agent (paid, cross-context unknown repo)
- `opus` = Opus directly (paid 5×, only for final review + commits + auth/crypto/spec interpretation)

---

### Phase 0 — repo bootstrap (1 day)

**T-000 — Bootstrap monorepo skeleton**
- Worker: **kimi**
- Scope: create directory tree from §6, empty `__init__.py`, `pyproject.toml` stubs, `package.json` stub, `LICENSE` (Apache-2.0), `.gitignore`, `Makefile` with empty targets
- Verification: `find . -type f` matches §6 tree; `git status` clean after commit
- Acceptance: tree exists; no business logic yet

**T-001 — Add Apache-2.0 license + CoC + Contributing + Security**
- Worker: **ollama** (one-shot, copy boilerplate)
- Scope: write top-level `LICENSE`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `CONTRIBUTING.md`, `SECURITY.md`
- Verification: files exist, license SPDX header at top of `LICENSE`
- Acceptance: standard text, no project-specific embellishment

**T-002 — Docker compose dev stack (Postgres + Valkey + ntfy)**
- Worker: **kimi**
- Scope: `docker-compose.yml` with Postgres 16, Valkey 8, ntfy 2 services + named volumes + healthchecks
- Verification: `docker compose up -d && docker compose ps` shows all healthy
- Acceptance: services reachable on stable ports (5432, 6379, 8000)

**T-003 — Devcontainer + Makefile install/dev/test/lint targets**
- Worker: **kimi**
- Scope: `.devcontainer/devcontainer.json` (Python 3.13 + Node 22 + uv + bun), Makefile targets that work locally and inside devcontainer
- Verification: `make install && make test` exits 0 on a fresh clone (no app code yet, so `make test` says "no tests yet")
- Acceptance: < 5 min to first `make dev` on a new machine

---

### Phase 1 — backend foundation (3 days)

**T-010 — Django project init + settings split**
- Worker: **kimi**
- Scope: `django-admin startproject config backend`, split settings into `base.py / dev.py / prod.py`, env-driven via `django-environ`, configure Postgres + Valkey
- Verification: `python manage.py check` exits 0; `python manage.py migrate` succeeds against the compose Postgres
- Acceptance: settings load cleanly from `.env`

**T-011 — DRF + Channels + Celery wiring**
- Worker: **kimi**
- Scope: install DRF, Channels (with channels-redis pointed at Valkey), Celery, define `asgi.py` + `celery.py`
- Verification: `daphne config.asgi:application` starts; `celery -A config worker` starts; a smoke task can be enqueued
- Acceptance: ASGI app runs WebSocket and HTTP

**T-012 — Apps scaffold (§5 models)**
- Worker: **kimi**
- Scope: create empty Django apps `accounts`, `hosts`, `projects`, `policies`, `threads`, `approvals`, `audit`, `skills`, `tier2`, `slash`, `adapters`; add to `INSTALLED_APPS`
- Verification: `python manage.py startapp` equivalent layout for each; `python manage.py check` exits 0
- Acceptance: each app has empty `models.py`, `admin.py`, `serializers.py`, `views.py`, `urls.py`, `tests/`

**T-013 — Models for accounts/hosts/projects/policies**
- Worker: **codex** (judgment on field types, validators, indexes)
- Scope: implement exactly the model fields in §5; add `clean()` validators; add Meta indexes on common lookups
- Verification: `python manage.py makemigrations && migrate` succeeds; `python manage.py test apps.accounts apps.hosts apps.projects apps.policies` for basic model-creation tests
- Acceptance: round-trip create/load works for each model; one test per model

**T-014 — Models for threads/messages/audit/approvals/skills + audit partitioning**
- Worker: **codex** (audit partitioning needs judgment)
- Scope: models per §5; audit table partitioned by month using `django-postgres-extra` or raw SQL in a `RunPython` migration
- Verification: insert 10k synthetic audit events spanning 3 months; query plan shows partition pruning
- Acceptance: partition pruning verified with `EXPLAIN`

**T-015 — Account vault (age-encrypted credential storage)**
- Worker: **opus** (security-sensitive — auth/crypto)
- Scope: `apps/accounts/vault.py` — generate age keypair at first run, store private key in `~/.config/agent-command-center/age.key` mode 0600, encrypt credential blobs with `pyrage`, decrypt only inside request scope, never log decrypted material
- Verification: unit tests cover round-trip; integration test confirms decrypted creds never appear in any log; a malicious admin view test confirms `repr(account)` hides the secret
- Acceptance: review by opus mandatory

**T-016 — REST endpoints for CRUD on accounts/hosts/projects/policies**
- Worker: **kimi** (mechanical DRF viewsets)
- Scope: `ModelViewSet` per resource, serializers per §5, `urls.py` routing, basic permission class `IsAuthenticated`
- Verification: `pytest -k api_crud` covers create/read/update/delete for each, plus permission rejection without auth
- Acceptance: OpenAPI schema generated by drf-spectacular

**T-017 — Auth (single-user mode: Django superuser)**
- Worker: **kimi**
- Scope: enable Django's auth + DRF token auth; one superuser per deployment; document multi-user as future work
- Verification: `python manage.py createsuperuser` works; token issued on login; auth required on all endpoints
- Acceptance: token in `Authorization: Token <...>` header

---

### Phase 2 — host daemon foundation (4 days)

**T-020 — host-agent package skeleton + config loader**
- Worker: **kimi**
- Scope: `host-agent/pyproject.toml`, `agent_host/__main__.py` entry, `config.py` loads from `~/.config/agent-command-center/host.toml` (backend URL, host slug, shared token)
- Verification: `python -m agent_host --help` runs
- Acceptance: empty daemon starts, reads config, exits cleanly

**T-021 — Transport: WebSocket client to backend**
- Worker: **codex** (judgment on reconnect/backoff)
- Scope: `transport.py` — persistent WebSocket to `wss://backend/host/<slug>/`, exponential backoff reconnect, heartbeat ping every 20s, message envelope: `{type, payload, msg_id, reply_to}`
- Verification: integration test with `pytest-asyncio` + a fake backend WS server; chaos test kills server, expects reconnect within 30s
- Acceptance: survives 100 random disconnects in CI

**T-022 — Heartbeat endpoint + Host model update**
- Worker: **kimi**
- Scope: backend `POST /api/hosts/{slug}/heartbeat/` updates `last_seen_at` + `status`; daemon calls it every 60s; backend marks host `offline` if no heartbeat for 180s (Celery beat)
- Verification: stop daemon, observe host transitions to offline in admin
- Acceptance: status accurate within 3 minutes

**T-023 — Runtime probes (Claude Code + Codex + Ollama)**
- Worker: **codex** (parsing CLI output; needs care for cross-platform)
- Scope: `runtime_probe/{claude_code,codex,ollama}.py` — detect installation, version, auth status; for Claude Code parse `claude --version` and `claude /status` (in non-interactive mode); for Codex parse `codex --version` and `codex login status`; for Ollama hit `http://localhost:11434/api/version`
- Verification: unit tests with mocked subprocess; integration test on this MacBook hits real CLIs
- Acceptance: returns `ProbeResult` with `auth_ok=True` for at least Claude + Codex on Phil's MacBook

**T-024 — PTY runner (Unix)**
- Worker: **codex** (PTY is fiddly)
- Scope: `pty/unix_runner.py` — `ptyprocess.PtyProcess.spawn`, async wrapper, write to stdin, read stdout/stderr, resize on terminal-size events from frontend
- Verification: spawn `bash`, send `echo hi`, assert "hi" in output
- Acceptance: handles 1MB/s output without blocking event loop

**T-025 — PTY runner (Windows)**
- Worker: **codex**
- Scope: `pty/windows_runner.py` using `pywinpty`; same interface as T-024
- Verification: spawn `cmd /c echo hi`, assert "hi"
- Acceptance: passes the same test suite as T-024 on a Windows runner

**T-026 — ANSI/terminal-state stripper**
- Worker: **codex**
- Scope: `pty/ansi_strip.py` — feed bytes into a `pyte` Screen, expose `text()` and `dirty_lines()`; expose clean message extraction for chat surfaces
- Verification: feed Claude Code's known welcome banner, assert no escape codes in output
- Acceptance: handles cursor moves, colors, clear-screen

---

### Phase 3 — Claude Code adapter (3 days)

**T-030 — Claude Code PTY adapter (Strategy B)**
- Worker: **codex**
- Scope: `adapters/claude_code_pty.py` implementing `RuntimeAdapter` — spawns `claude` with `--print` for one-shots, interactive for multi-turn, integrates pyte stripper, emits `NormalizedEvent`s
- Verification: unit tests with mocked PTY; integration test on Phil's MacBook actually runs Claude Code, sends "say hello", asserts "hello" in stream
- Acceptance: opus reviews diff for prompt-injection / log-leak risk

**T-031 — Claude Code RC-link adapter (Strategy A)**
- Worker: **codex**
- Scope: `adapters/claude_code_rc.py` — spawns `claude remote-control --spawn worktree --capacity 1 --sandbox --name "{thread.name}"`, parses stdout for session URL (regex), stores URL, emits a single `model_message` event with the URL, then monitors process; on exit, runs `git -C {worktree} diff` and emits a `diff` event
- Verification: integration test runs on Phil's MacBook, captures URL, asserts URL matches `https://claude.ai/code/...`
- Acceptance: refuses to start if `CLAUDE_CODE_OAUTH_TOKEN` is set in env (raises with friendly error)

**T-032 — Plan-eligibility precheck**
- Worker: **codex**
- Scope: before T-031 starts a session, run `claude --version` and confirm ≥ 2.1.51; run a probe to confirm RC enabled for this account; cache result for 1 hour
- Verification: simulated stub returning "Remote Control is not yet enabled" produces a clean rejection
- Acceptance: failure modes from the troubleshooting section of [official docs](https://code.claude.com/docs/en/remote-control) all produce specific error codes

---

### Phase 4 — Codex + Ollama + Aider adapters (3 days)

**T-040 — Codex exec adapter**
- Worker: **codex**
- Scope: `adapters/codex_exec.py` — `codex exec --ephemeral -s workspace-write -m gpt-5.5 "<prompt>"`; for each user message, run a fresh codex exec with conversation context passed in; parse stdout for tool calls and final assistant text
- Verification: integration test runs `codex exec` on Phil's MacBook with a trivial prompt, asserts non-empty response
- Acceptance: respects per-thread `sandbox` flag mapping to `-s read-only` when set

**T-041 — Codex PTY adapter (long-running)**
- Worker: **codex**
- Scope: PTY-supervise interactive `codex`; same pattern as Claude PTY
- Verification: integration; assert turn-by-turn message exchange works
- Acceptance: handles codex's TUI redraw without leaking control chars

**T-042 — Ollama HTTP adapter**
- Worker: **kimi**
- Scope: `adapters/ollama_http.py` — POST to `http://{host}:11434/api/chat` with `stream: true`, parse JSONL stream, emit message events; multi-account = multi-host (each thread bound to one Ollama base URL stored on Account)
- Verification: integration test against local Ollama (kimi-k2.6:cloud and gemma4:31b)
- Acceptance: streaming latency < 200ms first byte

**T-043 — Aider exec adapter**
- Worker: **kimi**
- Scope: `adapters/aider_exec.py` — `aider --message "..." --yes --no-stream` per message inside the project worktree; capture diff
- Verification: integration test on a sample repo
- Acceptance: respects worktree boundary

---

### Phase 5 — Tier 2 backend adapters (3 days)

Tier 2 adapters live in `backend/apps/tier2/` and never call the host daemon.

**T-050 — Anthropic API adapter**
- Worker: **codex**
- Scope: `tier2/anthropic.py` — uses `anthropic` SDK, streams via `client.messages.stream`, emits Channels-conformant events, decrypts account credential via vault
- Verification: integration test against Anthropic API with a low-cost prompt
- Acceptance: per-account credential isolation verified

**T-051 — OpenAI API adapter**
- Worker: **codex**
- Scope: `tier2/openai.py` — uses `openai` SDK, streams via Responses or Chat Completions
- Verification: integration test
- Acceptance: same as T-050

**T-052 — Gemini API adapter**
- Worker: **codex**
- Scope: `tier2/gemini.py` — uses `google-genai`
- Verification: integration test
- Acceptance: same

**T-053 — OpenRouter adapter (OpenAI-compatible base URL)**
- Worker: **kimi** (subclass of T-051 with different base URL)
- Scope: configurable base URL on Account; routes through OpenRouter
- Verification: integration test
- Acceptance: works with any OpenAI-compatible endpoint

---

### Phase 6 — Slash command middleware + WebSocket thread API (3 days)

**T-060 — Slash command parser (backend)**
- Worker: **codex**
- Scope: `apps/slash/parser.py` — parses message text, returns either `("text", original)` or `("slash", command, args)`; handlers in `apps/slash/handlers/`
- Verification: 30 parser unit tests
- Acceptance: nested slash arguments and quoted strings handled

**T-061 — Slash handlers: /fork /branch /diff /approve /reject /stop /host /account /model**
- Worker: **codex**
- Scope: one handler per command in §3.3 of [[agent-command-center-v4-universal-cockpit]]; each handler is a small async function that mutates the thread or enqueues a side-effect
- Verification: integration test per handler
- Acceptance: handlers never leak credentials in their output

**T-062 — Thread WebSocket consumer**
- Worker: **codex**
- Scope: `apps/threads/consumers.py` — Channels consumer subscribed per-thread; receives user input → routes through slash middleware → dispatches to Tier 1 (via host transport) or Tier 2 adapter; broadcasts events back
- Verification: end-to-end test: open WS, send message, receive streaming response
- Acceptance: backpressure handled (slow client doesn't block adapter)

---

### Phase 7 — Policy engine + approvals (2 days)

**T-070 — Command classifier**
- Worker: **codex**
- Scope: `apps/policies/classifier.py` — regex + allowlist tables → tier (low/medium/high/destructive); covers shell commands, git operations, package installers, network commands; loaded from `examples/policy-profiles/*.yaml`
- Verification: 100 known commands with expected tiers in fixture
- Acceptance: false-negative rate on destructive commands = 0%

**T-071 — Approval flow**
- Worker: **codex**
- Scope: when classifier flags High/Destructive, adapter pauses, emits `ApprovalRequest`, blocks until approved/rejected via WebSocket signal
- Verification: integration test: simulate high-risk command, assert thread waits, approve via API, assert command runs
- Acceptance: expiry behavior tested (auto-reject after `expires_at`)

**T-072 — Project sensitivity enforcement**
- Worker: **opus** (policy boundary — sensitive)
- Scope: thread creation refuses if requested account/runtime/host combo violates project policy; clear error message
- Verification: matrix test: 4 sensitivity levels × 3 account types × 2 runtime tiers
- Acceptance: opus reviews because this is the sovereignty moat

**T-073 — Secrets redactor**
- Worker: **opus** (security)
- Scope: regex patterns for known token formats (AWS, Stripe, GitHub PAT, Anthropic, OpenAI) + `.env` variable names + high-entropy strings; applied at audit ingest before persistence
- Verification: fixture of 50 secret-shaped strings, all redacted in output
- Acceptance: opus mandatory review

---

### Phase 8 — Frontend PWA (5 days)

**T-080 — Next.js scaffold + Tailwind + shadcn**
- Worker: **kimi**
- Scope: `npx create-next-app@latest --typescript --tailwind --app frontend`; install shadcn, configure dark mode
- Verification: `bun dev` serves at localhost:3000
- Acceptance: baseline page loads

**T-081 — Typed API client + auth**
- Worker: **codex**
- Scope: `lib/api.ts` — `openapi-typescript` codegen from drf-spectacular schema; login flow stores token in IndexedDB; auto-attach to every request
- Verification: smoke test against running backend
- Acceptance: types reflect §5 models

**T-082 — WebSocket hook**
- Worker: **codex**
- Scope: `lib/ws.ts` — `useThreadStream(threadId)` hook with reconnect, replay buffer, ordered delivery
- Verification: Playwright test simulating dropped connection
- Acceptance: zero message loss in ordered tests

**T-083 — Thread inbox page**
- Worker: **kimi** (mechanical UI from spec)
- Scope: `app/page.tsx` — inbox layout from §3.1 of V4 spec; thread cards with status pill, runtime badge, host badge, last-event timestamp; "+ New" opens new-thread flow
- Verification: visual: matches the ASCII mock in V4
- Acceptance: works on iPhone Safari (test with real device or BrowserStack)

**T-084 — Thread chat view**
- Worker: **codex** (chat UX is the moat — needs judgment)
- Scope: `app/thread/[id]/page.tsx` — message list (virtualized via `react-virtuoso`), composer with slash-command autocomplete, send streams tokens in via WS
- Verification: visual + interaction tests; specifically: type `/`, see autocomplete; type `/branch feature-x`, see slash handler ack
- Acceptance: opus reviews chat UX (it's the differentiator)

**T-085 — New thread flow**
- Worker: **codex**
- Scope: `components/thread/new-thread-modal.tsx` — runtime/host/account/project selector with policy-driven filtering (greyed-out incompatible combos)
- Verification: test the matrix from T-072
- Acceptance: invalid combos cannot be selected, not just rejected after submit

**T-086 — Approvals page + push subscription**
- Worker: **codex**
- Scope: `app/approvals/page.tsx` + ntfy subscription on service worker; tapping a notification opens the approval card; one-tap approve/reject
- Verification: end-to-end: trigger high-risk action, get notification on phone, approve from notification
- Acceptance: latency from event → push receipt < 5s

**T-087 — Hosts / Accounts / Audit / Skills admin pages**
- Worker: **kimi**
- Scope: CRUD pages mirroring backend, basic Tailwind tables and forms; no fancy UX needed
- Verification: form submit creates records
- Acceptance: editable from phone in pinch

**T-088 — PWA manifest + service worker + add-to-home**
- Worker: **kimi**
- Scope: `next-pwa` configured, `manifest.json`, icons, offline-fallback page
- Verification: Lighthouse PWA audit passes
- Acceptance: installable on iOS Safari and Android Chrome

---

### Phase 9 — Observability + ops (2 days)

**T-090 — Structured logging with OpenTelemetry**
- Worker: **codex**
- Scope: configure OTel SDK in backend and host-agent; emit JSON logs to Loki via OTLP
- Verification: a request shows trace+log correlation in Grafana
- Acceptance: never logs decrypted credentials (covered by T-073 redactor)

**T-091 — Prometheus metrics**
- Worker: **kimi**
- Scope: `django-prometheus` + custom metrics: threads_active, messages_per_minute_per_thread, approvals_pending, adapter_errors_total{adapter}
- Verification: `/metrics` endpoint serves Prom format
- Acceptance: scraped by compose-stack Prom

**T-092 — Grafana dashboards**
- Worker: **ollama** (one-shot JSON generation)
- Scope: `deploy/observability/grafana/dashboards/{overview,threads,approvals,adapters}.json`
- Verification: dashboards import cleanly
- Acceptance: covers system health at a glance

**T-093 — Backup + retention scripts**
- Worker: **kimi**
- Scope: nightly `pg_dump` to encrypted file (age) + optional rclone to S3-compatible; configurable retention; audit table partitions dropped after N months per PolicyProfile
- Verification: backup → restore roundtrip on a copy
- Acceptance: documented in README

---

### Phase 10 — Docs + release (3 days)

**T-100 — Docusaurus site scaffold + landing**
- Worker: **kimi**
- Scope: `docs/` with sidebar, intro, quickstart, architecture
- Verification: `cd docs && bun run start` serves
- Acceptance: published via GitHub Pages from `gh-pages` branch

**T-101 — Quickstart guide**
- Worker: **opus** (user-facing copy — opus writes once, never again)
- Scope: 15-minute setup from clone to first chat with Claude Code on a MacBook
- Verification: a new person follows it and reports issues
- Acceptance: external user can complete it

**T-102 — Architecture page (mirror §1, §5, §6 of this spec)**
- Worker: **ollama** (summarisation)
- Scope: distill spec sections into docs page
- Verification: links work, diagrams render
- Acceptance: accurate

**T-103 — Adapter authoring guide**
- Worker: **codex** (technical doc)
- Scope: how to write a new Tier 1 or Tier 2 adapter against the §7 contract
- Verification: external dev can add a new adapter in < 1 day
- Acceptance: reference impl is at most 400 lines

**T-104 — Security disclosure policy + threat model**
- Worker: **opus** (security-sensitive)
- Scope: `SECURITY.md` + `docs/threat-model.md` documenting trust boundaries, encryption at rest, what an attacker can/cannot reach
- Verification: external security review (or self-review with checklist)
- Acceptance: opus mandatory

**T-105 — Public release: GitHub repo creation, tag v0.1.0, blog post**
- Worker: **opus** (one-time, communications + commit message)
- Scope: create public repo, push, announce
- Verification: clone-build-run works from public repo
- Acceptance: external first-time user can `make install && make dev` in < 15 min

---

## 9. Routing summary

```
~50 tasks total:
  ~24 → kimi (mechanical scaffolds, CRUD, UI tables, JSON dashboards)
  ~18 → codex (adapters, PTY, chat UX, slash handlers, classifiers)
  ~3 → ollama (one-shot text, boilerplate)
  ~0 → sonnet (no task warrants it in this spec)
  ~5 → opus (vault, secrets, sensitivity policy, security docs, release)
```

Target distribution = ≥80% non-Opus, matches the `model-routing.md` ≥60% target with margin.

Each PR opened by a delegated worker triggers an opus review pass. Opus may:
- approve and squash-merge
- send back to same worker with notes
- escalate (rare: only if cross-cutting design issue surfaces)

---

## 10. Out of scope for V5 MVP

- Cursor / Windsurf / Antigravity / Office Copilot chat aggregation (vendor-blocked, see V4 §1)
- Multi-user / RBAC / SSO
- Marketplace, plugin SDK
- Custom agent runtime (LangGraph etc.)
- Voice (ElevenLabs adapter is Phase 2)
- Salesforce / SAP / OfficeLabs business adapters (Phase 3)
- iOS native app (PWA only)
- Hosted SaaS

---

## 11. Codex review checklist

Codex should review this spec with this prompt:

```
You are reviewing a build spec for an open-source AI Agent Command Center.
The spec is in agent-command-center-v5-build-spec.md.

Focus on:

1. CORRECTNESS of the Claude Code Remote Control assumptions in §0 and §2,
   against https://code.claude.com/docs/en/remote-control. Flag any claim
   that contradicts the official docs.

2. FEASIBILITY of the Tier 1 PTY supervision pattern across macOS, Linux,
   and Windows for Claude Code (TUI-heavy), Codex (TUI), Ollama (no TUI),
   Aider (CLI). Specifically: will pyte handle the TUI cleanly? What
   alternatives if not?

3. SECURITY GAPS in §1.2 (stack), §5 (data model — especially Account.encrypted_credential),
   §7 (adapter contract), and the opus-reviewed security tasks (T-015, T-072, T-073, T-104).
   Identify anything we're missing.

4. STACK CHOICES in §1.2 — any free/OSS component that's a poor fit, any
   missing critical dep, license concerns for an Apache-2.0 repo.

5. DELEGATION DECISIONS in §8 — for each task, is the assigned worker tier
   appropriate, or would you reassign? Specifically watch for tasks marked
   kimi that should be codex (judgment) or codex that should be opus
   (security).

6. SCOPE — anything missing for an MVP that would block opening the repo
   to external contributors?

Return your review as a numbered list of findings. For each finding, give:
   - severity: blocker | high | medium | low
   - location: section number or task ID
   - what's wrong
   - suggested fix

Do not rewrite the spec. Just review.
```

This is run via:
```bash
codex exec --ephemeral -s read-only -m gpt-5.5 \
  "$(cat agent-command-center-v5-build-spec.md && echo --- && cat review-prompt.txt)"
```

Captured into `09 Research/2026-05-25-codex-review-of-v5-spec.md`.

## Backlinks
- [[Ideas — Project Overview]]
- [[Ideas - index]]
- [[agent-command-center-v4-universal-cockpit]]
- [[agent-command-center-v3-sharpened]]
- [[agent-command-center-v2-archived]]
- [[2026-05-25-agent-command-center-market-review]]
