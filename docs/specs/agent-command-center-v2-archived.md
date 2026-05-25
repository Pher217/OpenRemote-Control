---
archived_from: ai-agent-command-center-spec-v2.md
archived_date: 2026-05-25
status: superseded — see [[agent-command-center-v3-sharpened]] and [[2026-05-25-agent-command-center-market-review]]
---

> Archived verbatim from `C:\Users\philh\Downloads\ai-agent-command-center-spec-v2.md` on 2026-05-25. Superseded by V3 after the May 2026 market scan. Kept for reference.

## Backlinks
- [[Ideas — Project Overview]]
- [[Ideas - index]]
- [[agent-command-center-v3-sharpened]]
- [[2026-05-25-agent-command-center-market-review]]

---

# AI Agent Command Center — Deep Research Review & V2 Product Architecture

**Date:** 2026-05-25  
**Owner context:** Philippe Hermann — Schatzi AI / OfficeLabs / local-first agent workflows  
**Purpose:** replace the first research note with a deeper, more accurate technical review and a buildable V2 architecture for a cross-agent command center.

---

## 0. Executive decision

The product you are imagining is real as a market gap, but the best implementation is **not** to build a new coding agent first.

The right product is a **control plane**:

> a secure, local-first command layer that knows which machines, projects, repositories, documents, and agent runtimes are available; starts or attaches to sessions; routes work to the correct runtime; captures logs, diffs, approvals, and results; and keeps a permanent audit trail.

The important correction from the first version is this:

> Claude Code and Codex are no longer only “normal local CLIs”. Both are moving toward remote/session control. The custom product should not compete with their remote-control surfaces. It should sit **above** them as a cross-runtime registry, policy engine, session router, approval layer, and audit system.

So the V2 strategy is:

```text
Do not rebuild Claude Code Remote Control.
Do not rebuild Codex Mobile / Codex remote-control.
Do not rebuild GitHub Agent HQ.

Build the missing layer between them:
- one project registry;
- one host registry;
- one policy model;
- one approval inbox;
- one audit trail;
- one mobile dashboard;
- multiple runtime adapters.
```

---

## 1. Final verdict on existing tools

There is still **no single mature open-source product** that fully satisfies the spec:

```text
One mobile/web app
→ controls Claude Code, Codex, local agents, GitHub agents, Cline, OpenHands, Aider, custom scripts
→ across Mac/Windows/VPS
→ with persistent sessions
→ with local-first privacy
→ with approval gates
→ with unified audit logs
→ with Git branch/worktree isolation
→ with a serious production security model.
```

But the building blocks are now stronger than expected.

### Closest current pieces

| Layer | Strongest candidate | What it gives | Why it is not enough alone |
|---|---|---|---|
| Local premium coding session | Claude Code Remote Control | Local Claude Code process controlled from phone/browser | Claude-only, account/OAuth-bound, not a universal control API |
| OpenAI local coding runtime | Codex CLI / Codex app | Local coding agent, remote-control direction, mobile/computer-use direction | OpenAI-native, still not a universal orchestrator |
| GitHub cloud tasks | GitHub Agent HQ / Copilot cloud agent / Claude / Codex agents | Issue/PR-based cloud agent sessions across GitHub, GitHub Mobile, VS Code | Cloud execution, GitHub-bound, not sovereignty-first |
| Open-source programmable software agents | OpenHands Software Agent SDK | Python/REST APIs, local or Docker/Kubernetes workspaces | More runtime/SDK than polished command center |
| Open-source agent runtime / editor/terminal agent | Cline + Cline SDK + Kanban | Editor/terminal agent, SDK, approvals, parallel Kanban worktrees | Strong candidate, but not your full cross-provider enterprise control layer |
| Terminal pair programmer | Aider | Mature local git-repo editing loop, many model providers | Useful worker, not a multi-agent command center |
| PR quality gate | Continue | AI checks as markdown files, GitHub status checks | Review layer only, not a session manager |
| Network access | Tailscale Serve / SSH / ACLs | Private access to local services and machines | Connectivity/security only, not orchestration |
| Business automation | n8n / Windmill | Workflows, triggers, integrations | Not safe as the source of truth for code-agent authority |
| Multi-agent orchestration framework | LangGraph / CrewAI / AutoGen | Graphs, crews, multi-agent workflows | Useful inside custom agents, not a remote-control product |

---

## 2. What changed versus the first note

The first note was directionally correct but too simple in three places.

### 2.1 Claude Code Remote Control is more specific than “API calls”

Claude Code Remote Control connects `claude.ai/code` or the Claude mobile app to a **Claude Code session running on your machine**. Anthropic’s docs explicitly say the web/mobile interfaces are just a window into the local session, with filesystem, MCP servers, tools, and project configuration staying local. API keys are not supported for Remote Control; it requires Claude.ai OAuth.

Implication:

```text
Your hub should not try to impersonate Claude Code Remote Control.
It should launch, monitor, name, and track Claude Code sessions, then link/attach to the official remote-control surface where useful.
```

Useful Claude Code facts from the current docs:

```text
claude remote-control
claude --remote-control
/remote-control inside an existing session
```

And Claude Code Remote Control supports server mode and spawn modes, including `same-dir`, `worktree`, and `session`. This matters because **worktree isolation is already partly native** in Claude Code Remote Control server mode.

### 2.2 Codex is moving quickly toward a similar control model

OpenAI’s Codex repository describes Codex CLI as a local coding agent. Current release notes mention:

```text
codex remote-control
foreground command behavior
readiness reporting
machine status
explicit daemon-style start/stop commands
permission profiles
lifecycle events
subagent start/stop events
```

OpenAI’s Codex changelog also shows a strong app/mobile/computer-control direction: Appshots, Goal Mode, remote computer use, mobile, plugins, and marketplace sources.

Implication:

```text
Codex should be treated as a fast-moving runtime with feature detection.
The adapter must inspect installed version/capabilities at runtime.
Do not hardcode assumptions around Codex command behavior.
```

### 2.3 Cline is closer than initially weighted

Cline is no longer only a VS Code extension. Its docs now position it as:

```text
editor agent
terminal agent
SDK
Kanban-style parallel agents
worktrees
auto-commit
dependency chains
human-in-the-loop approvals
```

This makes Cline one of the closest open-source reference projects for the “many agents in parallel” part of your idea.

Implication:

```text
Before building your own parallel task board, inspect Cline Kanban and Cline SDK.
You may reuse ideas or possibly integrate it as a runtime.
```

---

## 3. Product definition

### Working name

```text
Agent Command Center
```

Better product description:

> A private control plane for supervising AI agent sessions across local machines, cloud agents, repositories, documents, and automation tools.

It is not:

```text
not another LLM wrapper;
not a replacement for Claude Code;
not a replacement for Codex;
not a clone of GitHub Agent HQ;
not a visual workflow toy;
not an IDE.
```

It is:

```text
an operations layer for AI work.
```

The analogy:

```text
Tailscale for secure machine reachability
+ GitHub for reviewable code work
+ Claude Code / Codex for agent execution
+ Django admin for operational governance
+ mobile approval inbox for steering long-running sessions
```

---

## 4. The core insight

Every useful coding/document agent has the same operational lifecycle:

```text
1. select workspace/project
2. create or attach to an execution environment
3. receive a task
4. reason and act
5. read files / use tools / run commands
6. request approvals for risky actions
7. produce artifacts: diff, patch, PR, document, report, logs
8. wait for feedback
9. continue, stop, archive, or merge
```

The current market problem is that each agent owns this lifecycle separately.

Your opportunity is to create a shared operational model:

```text
AgentHost
Project
Runtime
Session
Task
Event
Approval
Artifact
Policy
AuditLog
```

Once this model exists, the underlying runtime can vary.

---

## 5. Recommended V2 architecture

### 5.1 Architecture diagram

```text
┌───────────────────────────────────────────────────────────────┐
│                         Clients                               │
│  Mobile PWA / Web dashboard / CLI / later VS Code extension   │
└────────────────────────────┬──────────────────────────────────┘
                             │ HTTPS/WebSocket, preferably private
                             ▼
┌───────────────────────────────────────────────────────────────┐
│                    Agent Control Backend                       │
│  Django / DRF / Channels                                      │
│  Project registry                                             │
│  Host registry                                                │
│  Runtime registry                                             │
│  Session registry                                             │
│  Policy engine                                                │
│  Approval inbox                                               │
│  Audit trail                                                  │
└─────────────┬───────────────────────┬─────────────────────────┘
              │                       │
              ▼                       ▼
┌───────────────────────┐   ┌───────────────────────────────┐
│ PostgreSQL             │   │ Redis / Celery / Channels      │
│ sessions/tasks/events  │   │ async jobs / streaming         │
│ approvals/artifacts    │   │ notifications                  │
└───────────────────────┘   └───────────────────────────────┘
              │
              │ secure host channel over Tailscale / mTLS
              ▼
┌───────────────────────────────────────────────────────────────┐
│                       Agent Host Daemon                        │
│  Runs on MacBook / Windows workstation / VPS / lab server      │
│  Owns child processes, PTYs, worktrees, containers, git state  │
└─────┬─────────────┬──────────────┬──────────────┬─────────────┘
      │             │              │              │
      ▼             ▼              ▼              ▼
┌───────────┐ ┌───────────┐ ┌────────────┐ ┌────────────────────┐
│ Claude    │ │ Codex     │ │ OpenHands  │ │ Cline / Aider /    │
│ Code CLI  │ │ CLI/App   │ │ SDK/server │ │ scripts / Office   │
└───────────┘ └───────────┘ └────────────┘ └────────────────────┘
              │
              ▼
       ┌─────────────────────┐
       │ Optional cloud layer │
       │ GitHub Agent HQ      │
       │ Copilot cloud agent  │
       │ Claude/Codex agents  │
       └─────────────────────┘
```

### 5.2 Design principle

```text
The backend does not become the agent.
The backend governs the agents.
```

The control backend should decide:

```text
which host can run this task;
which runtime is allowed for this project;
which files/paths are allowed;
which model/provider is allowed;
which commands need approval;
which artifacts must be captured;
which events must be audited;
which results may be pushed or shared.
```

The runtime should decide:

```text
how to reason;
how to edit;
how to use its own tools;
how to interact with Claude/Codex/OpenHands/Cline internals.
```

---

## 6. Two possible implementation strategies

### Strategy A — “thin supervisor”

This is the recommended first implementation.

```text
The host daemon launches official runtimes.
It does not deeply modify them.
It streams logs and captures artifacts.
It maintains external session metadata.
It creates worktrees and branches.
It applies policy before/after runtime actions where possible.
```

Advantages:

```text
fast to build;
low compatibility risk;
uses best-in-class agents directly;
can adapt as tools evolve;
works with Claude Code / Codex / Aider quickly.
```

Limitations:

```text
some runtime internals remain opaque;
approval capture may be imperfect if CLI output is not structured;
state restoration depends on runtime support;
provider remote-control flows remain provider-owned.
```

### Strategy B — “native agent runtime”

This uses OpenHands SDK, Cline SDK, LangGraph, or CrewAI to build your own deeply integrated agents.

Advantages:

```text
full event model;
structured approvals;
better observability;
custom tools;
better enterprise integration;
easier sovereignty story.
```

Limitations:

```text
slower to build;
you must own more agent behavior;
you compete with Claude Code/Codex quality;
you need strong evals and guardrails.
```

### Recommendation

```text
Phase 1: thin supervisor.
Phase 2: add native runtime for custom/internal agents.
```

Do not start with Strategy B unless you specifically need an internal Schatzi/OfficeLabs-owned agent runtime.

---

## 7. Layered tool review

### 7.1 Claude Code

**Best role:** premium local reasoning/editing runtime.

Relevant current capabilities:

```text
local project context;
filesystem access;
MCP servers;
tools and project configuration;
Remote Control from browser/mobile;
server mode;
interactive remote-control flag;
remote session from existing session;
worktree spawn mode;
OAuth requirement for remote sessions.
```

Best integration pattern:

```text
Use AgentHostDaemon to start Claude Code sessions.
Prefer official Remote Control for human remote interaction.
Store external metadata in your backend.
Use git worktree/branch isolation even if Claude can also spawn worktrees.
Track session name, project path, branch, runtime version, and output artifacts.
```

Avoid:

```text
scraping remote-control tokens;
assuming Remote Control is a public API;
expecting API-key auth to work for Remote Control;
relying on undocumented internal session files as source of truth.
```

### 7.2 Codex CLI / Codex app

**Best role:** OpenAI-native local coding runtime and increasingly mobile/remote-capable coding agent.

Relevant current capabilities:

```text
local CLI coding agent;
Codex app direction;
Goal Mode for longer-running objectives;
remote computer use;
remote-control CLI direction;
permission profiles;
plugins/marketplace direction;
lifecycle event hooks direction.
```

Best integration pattern:

```text
Build a version-aware Codex adapter.
Feature-detect `codex remote-control`, daemon commands, permission profiles, event hooks.
Treat Codex as high-value runtime, not a black-box API call.
```

Avoid:

```text
hardcoded command assumptions;
assuming feature stability;
using Codex as the only orchestration layer;
using remote computer use on sensitive work without strong local policy.
```

### 7.3 GitHub Agent HQ / Copilot cloud agent / third-party agents

**Best role:** cloud PR worker for GitHub-native tasks.

Relevant current capabilities:

```text
agents launched from GitHub, GitHub Mobile, VS Code;
Copilot cloud agent in GitHub Actions-powered environment;
third-party agents such as Claude and Codex in public preview;
issue/prompt assignment;
branch and PR workflow;
review loop through PR comments;
usage of Actions minutes and premium requests.
```

Best integration pattern:

```text
Use as optional external workers.
Create issue/task from your backend.
Track GitHub PR/session status.
Mirror results into your audit trail.
Allow only for projects classified as public/internal, not confidential/regulated by default.
```

Avoid:

```text
making GitHub cloud execution the trust foundation for Schatzi-sensitive code;
using it for Swiss-sovereignty-sensitive client repositories;
automatic merge;
no local review.
```

### 7.4 Cline / Cline SDK / Kanban

**Best role:** open-source agent runtime candidate and parallel task-board reference.

Relevant current capabilities:

```text
editor and terminal agent;
file read/write;
terminal commands;
browser use;
explicit approval;
SDK;
CLI;
Kanban with parallel agents;
per-card worktrees;
auto-commit;
dependency chains;
IDE plugins.
```

Best integration pattern:

```text
Evaluate Cline SDK before building your own native agent core.
Evaluate Kanban before building a parallel task board.
Potentially wrap Cline as a runtime adapter.
```

Cline is important because it overlaps strongly with your idea. It may not replace the control plane, but it can reduce the amount you need to build.

### 7.5 OpenHands Software Agent SDK

**Best role:** programmable open-source software-agent runtime.

Relevant current capabilities:

```text
Python APIs;
REST APIs;
agents for code tasks;
local workspace;
ephemeral Docker/Kubernetes workspaces through Agent Server;
use cases from README generation to multi-agent refactors.
```

Best integration pattern:

```text
Use OpenHands where you want your own controlled agent runtime.
Use it for repeatable maintenance agents, dependency update agents, test-fixing agents, or larger multi-agent jobs.
```

### 7.6 Aider

**Best role:** simple, reliable terminal pair-programming worker.

Relevant capabilities:

```text
terminal pair programming;
local git repo editing;
many model providers;
chat modes;
config files;
lint/test support;
notifications;
browser use.
```

Best integration pattern:

```text
Use Aider for small constrained edits, patch proposals, cheap local model workflows, and non-UI terminal jobs.
```

### 7.7 Continue

**Best role:** review and quality gates.

Relevant capabilities:

```text
AI checks on pull requests;
checks defined as markdown files in repo;
GitHub status checks;
suggested fixes when checks fail.
```

Best integration pattern:

```text
After any agent creates a branch/PR, run Continue checks.
Store pass/fail status in your dashboard.
Use it as a standardized “anti-slop” review layer.
```

### 7.8 LangGraph / CrewAI / AutoGen

**Best role:** internal orchestration framework, not the command center itself.

Use them when you build custom agents such as:

```text
repo analyst;
release-note generator;
security reviewer;
Office document editor;
S&OP report generator;
Matisa engineering data checker;
Schatzi onboarding automation;
agent evaluation loops.
```

Recommendation:

```text
Use LangGraph if you need durable state and human-in-the-loop graph control.
Use CrewAI if you want simple multi-role agent workflows.
Use AutoGen mainly if you already have a reason to align with Microsoft’s agent ecosystem.
```

### 7.9 n8n / Windmill

**Best role:** external workflow automation.

Use for:

```text
notifications;
Slack/Teams alerts;
CRM notes;
scheduled reports;
admin workflows;
webhook reactions.
```

Do not use as:

```text
source of truth for code-agent authority;
security policy engine;
repo permission system;
critical approval ledger.
```

### 7.10 Tailscale

**Best role:** private connectivity and access control.

Recommended pattern:

```text
Use Tailscale Serve for private dashboard access inside your tailnet.
Use Tailscale SSH for secure host administration.
Use ACLs/grants with least privilege.
Avoid Tailscale Funnel for the first version unless you intentionally need public internet exposure.
```

---

## 8. Decision matrix

| Task type | Recommended runtime | Reason |
|---|---|---|
| Complex architecture/refactor | Claude Code local | Strong reasoning, local environment, MCP |
| OpenAI-native coding task | Codex local | Strong OpenAI runtime, local execution, mobile direction |
| Long objective from mobile | Codex Goal Mode / Claude RC | Native remote-control/mobile surfaces |
| GitHub issue implementation | GitHub cloud agent / Agent HQ | Native issue → branch → PR loop |
| Parallel small tasks | Cline Kanban or custom hub phase 2 | Worktrees and task-board model |
| Repeatable internal maintenance | OpenHands SDK | Programmable, local/container workspaces |
| Simple patch/edit | Aider | Fast terminal workflow |
| Pull request quality gate | Continue | Repo-defined AI checks |
| Notification/ops workflow | n8n/Windmill | Integrations and alerts |
| Sensitive client/Schatzi code | Local runtime only | Sovereignty/privacy control |
| Public OSS contribution | GitHub agent acceptable | Lower sensitivity, PR reviewable |

---

## 9. Recommended product scope

### 9.1 MVP should do only five things well

```text
1. Register projects and hosts.
2. Start/attach to agent sessions.
3. Show live status and logs.
4. Manage approval requests.
5. Capture artifacts: branch, diff, PR, transcript, test output.
```

That is enough to create real value.

### 9.2 MVP should not do these yet

```text
No SaaS multi-tenant product.
No public exposure.
No custom LLM gateway.
No fine-grained process sandbox across all runtimes.
No full IDE in the phone.
No Excel/Office agent until coding-agent lifecycle is stable.
No automatic merge to main.
No autonomous production deployments.
```

### 9.3 V2 product positioning

```text
Local-first agent operations console.
```

More precise:

> Control Claude Code, Codex, Cline, OpenHands, Aider, GitHub agents, and custom workers from one private dashboard — with project policies, approvals, branches, logs, and audit history.

---

## 10. Reference implementation stack

Given your existing stack, the most natural implementation is:

```text
Backend:
Django
Django REST Framework
PostgreSQL
Redis
Celery
Django Channels / WebSockets

Frontend:
Next.js PWA
Tailwind
Mobile-first approval UI

Host daemon:
Python
asyncio
ptyprocess or subprocess + PTY handling
Git CLI wrapper
Docker SDK where needed
Tailscale identity awareness

Connectivity:
Tailscale Serve for private dashboard access
Tailscale SSH for host administration
ACLs/grants for least privilege

Runtimes:
Claude Code adapter
Codex adapter
Cline adapter / SDK later
OpenHands adapter later
Aider adapter later
GitHub cloud-agent adapter later

Review:
Git worktrees
Branch isolation
Continue checks
PR templates
No-main-branch guard

Observability:
Structured logs
AgentEvent table
Artifact table
Session transcript
Optional Langfuse for agent telemetry
Optional promptfoo/evals for agent quality
```

---

## 11. Data model V2

### 11.1 AgentHost

```text
id
name
machine_type              # macbook, windows_workstation, linux_vps, lab_server
os
hostname
tailscale_dns_name
tailscale_node_id
status                    # online, offline, degraded, disabled
last_seen_at
capabilities_json
allowed_roots_json        # allowed filesystem roots
created_at
updated_at
```

### 11.2 Runtime

```text
id
host_id
runtime_type              # claude_code, codex, cline, openhands, aider, shell, github_agent
runtime_version
capabilities_json
supports_remote_control
supports_structured_events
supports_worktree_mode
status                    # available, unavailable, auth_required, disabled
last_checked_at
config_json
```

### 11.3 Project

```text
id
name
slug
repo_url
local_path
default_branch
sensitivity_level         # public, internal, confidential, regulated
allowed_hosts_json
allowed_runtimes_json
allowed_models_json
policy_profile_id
created_at
updated_at
```

### 11.4 PolicyProfile

```text
id
name
sensitivity_level
allow_cloud_agents
allow_external_models
require_worktree
require_approval_for_push
require_approval_for_package_install
require_approval_for_network_commands
block_destructive_commands
max_runtime_minutes
max_parallel_sessions
secrets_redaction_enabled
created_at
updated_at
```

### 11.5 AgentSession

```text
id
project_id
host_id
runtime_id
session_name
external_session_ref
status                    # pending, starting, running, waiting_for_user, failed, completed, stopped
started_by
started_at
ended_at
last_event_at
branch_name
worktree_path
remote_control_url_hash   # do not store raw provider session URL unless necessary
metadata_json
```

### 11.6 AgentTask

```text
id
session_id
title
prompt
acceptance_criteria
priority
status
created_by
created_at
completed_at
```

### 11.7 AgentEvent

```text
id
session_id
event_type                # stdout, stderr, model_message, command, tool_call, approval_request, diff, test, error
payload_json
redacted_payload_text
sequence_no
created_at
```

### 11.8 ApprovalRequest

```text
id
session_id
request_type              # run_command, edit_file, install_package, network_access, push_branch, open_pr, deploy
risk_level                # low, medium, high, destructive
summary
payload_json
preview_text
status                    # pending, approved, rejected, expired
approved_by
approved_at
expires_at
```

### 11.9 Artifact

```text
id
session_id
artifact_type             # diff, patch, transcript, log, screenshot, test_report, pr_link, generated_file
path_or_url
checksum
metadata_json
created_at
```

### 11.10 AuditLog

```text
id
actor
action
object_type
object_id
ip_or_tailnet_identity
payload_json
created_at
```

---

## 12. Runtime adapter contract

Every runtime should implement a common interface.

```python
class AgentRuntimeAdapter:
    runtime_type: str

    def probe(self) -> RuntimeProbeResult:
        """Detect installation, version, auth status, and capabilities."""

    async def start_session(self, request: StartSessionRequest) -> SessionHandle:
        """Start or attach to a runtime session."""

    async def send_message(self, session: SessionHandle, message: str) -> None:
        """Send task/message to the active runtime."""

    async def stream_events(self, session: SessionHandle):
        """Yield normalized AgentEvent objects."""

    async def request_stop(self, session: SessionHandle) -> None:
        """Ask runtime to stop gracefully."""

    async def kill(self, session: SessionHandle) -> None:
        """Hard kill if graceful stop fails."""

    async def collect_artifacts(self, session: SessionHandle) -> list[ArtifactRef]:
        """Collect diff, transcript, test output, logs, PR links."""
```

### Adapter maturity levels

```text
Level 0 — external link only
The backend opens/records a link to the provider’s native remote-control session.

Level 1 — process supervisor
The host daemon launches CLI processes, streams stdout/stderr, manages branches/worktrees.

Level 2 — structured runtime adapter
The runtime exposes structured events, approvals, artifacts, state.

Level 3 — native agent runtime
The control plane owns the agent loop through OpenHands/Cline SDK/LangGraph/custom tools.
```

Recommended start:

```text
Claude Code: Level 1 + external link to official RC
Codex: Level 1, then Level 2 as remote-control/event hooks mature
GitHub Agent HQ: Level 0/2 via GitHub API and PR status
OpenHands: Level 3 candidate
Cline SDK: Level 3 candidate
Aider: Level 1
```

---

## 13. Host daemon design

The host daemon is the most important technical component.

### Responsibilities

```text
register host with backend;
probe installed runtimes;
keep heartbeat;
receive approved session-start commands;
create git worktree and branch;
launch runtime process;
stream events to backend;
watch for file changes/diffs;
classify commands where possible;
stop/kill sessions;
collect artifacts;
clean up worktrees;
report failures.
```

### Host daemon internal modules

```text
agent_host/
  main.py
  config.py
  heartbeat.py
  backend_client.py
  runtime_registry.py
  adapters/
    claude_code.py
    codex.py
    cline.py
    aider.py
    openhands.py
    shell.py
  process/
    pty_runner.py
    subprocess_runner.py
    lifecycle.py
  git/
    worktree.py
    branch.py
    diff.py
    safe_git.py
  policy/
    command_classifier.py
    path_guard.py
    secrets_redactor.py
    risk_rules.py
  streaming/
    event_normalizer.py
    websocket_client.py
  artifacts/
    collector.py
    checksum.py
```

---

## 14. Security model

### 14.1 Non-negotiable rules

```text
No direct edits on main.
No automatic merge.
No production deployment by default.
No destructive command without explicit approval.
No public dashboard exposure in MVP.
No plaintext model/provider credentials.
No raw remote-control URLs stored unless encrypted and time-limited.
No agent access to directories outside project allowlist.
No cloud agent for confidential/regulated projects by default.
```

### 14.2 Command risk tiers

| Tier | Examples | Default behavior |
|---|---|---|
| Low | `git status`, `grep`, `pytest`, `npm test`, read files | allow + log |
| Medium | edit files, generate migrations, install dev package | policy-dependent approval |
| High | `git push`, open PR, change infra files, modify CI, network exfil risk | explicit approval |
| Destructive | `rm -rf`, drop DB, rotate secrets, deploy prod, delete branch | blocked by default |

### 14.3 Project sensitivity policy

| Sensitivity | Cloud agents | External models | Local runtime | Approval strictness |
|---|---:|---:|---:|---:|
| Public | allowed | allowed | allowed | normal |
| Internal | limited | limited | preferred | elevated |
| Confidential | blocked by default | restricted | required | strict |
| Regulated | blocked | approved-only | required | very strict |

### 14.4 Tailscale policy

Recommended MVP:

```text
All clients and hosts join the same tailnet.
Dashboard exposed via Tailscale Serve only.
Host administration via Tailscale SSH.
ACLs/grants deny by default.
Only your user/device can reach backend and host daemon ports.
No Funnel by default.
```

---

## 15. Mobile UX V2

Mobile should not be a mini IDE. It should be an **approval and steering console**.

### Home

```text
Active sessions
Waiting approvals
Hosts online/offline
Failed sessions
Recently completed tasks
```

### Session detail

```text
Project
Runtime
Host
Branch/worktree
Current status
Last agent message
Live event stream
Test status
Files changed summary
Stop button
```

### Approval card

```text
Action requested
Risk level
Command/diff preview
Reason from agent
Approve once
Reject
Approve similar for this session
Open desktop review
```

### Review screen

```text
Changed files
Diff summary
Test output
Generated artifacts
Open PR
Ask agent to revise
Archive session
```

Design principle:

```text
Mobile = decide, approve, steer, stop.
Desktop = inspect deeply, edit manually, merge.
```

---

## 16. Repo structure

```text
agent-command-center/
  backend/
    manage.py
    config/
    apps/
      accounts/
      hosts/
      runtimes/
      projects/
      sessions/
      tasks/
      approvals/
      artifacts/
      audit/
      policies/
      github_integration/

  host-agent/
    pyproject.toml
    agent_host/
      main.py
      adapters/
      process/
      git/
      policy/
      streaming/
      artifacts/
      tests/

  frontend/
    package.json
    app/
    components/
      session-card.tsx
      approval-card.tsx
      host-status.tsx
      diff-summary.tsx
    lib/

  infra/
    docker-compose.yml
    tailscale/
    pulumi/

  docs/
    architecture.md
    security-model.md
    runtime-adapter-contract.md
    host-daemon.md
    mvp-roadmap.md
```

---

## 17. API sketch

```text
GET    /api/hosts/
POST   /api/hosts/register/
POST   /api/hosts/{id}/heartbeat/

GET    /api/projects/
POST   /api/projects/

GET    /api/runtimes/
POST   /api/runtimes/probe/

GET    /api/sessions/
POST   /api/sessions/start/
GET    /api/sessions/{id}/
POST   /api/sessions/{id}/message/
POST   /api/sessions/{id}/stop/
POST   /api/sessions/{id}/collect-artifacts/

GET    /api/sessions/{id}/events/
WS     /ws/sessions/{id}/events/

GET    /api/approvals/
POST   /api/approvals/{id}/approve/
POST   /api/approvals/{id}/reject/

GET    /api/artifacts/{id}/
GET    /api/audit/
```

---

## 18. MVP build plan

### Phase 0 — reality spike

Goal:

```text
Prove process supervision works for Claude Code and Codex on your real machines.
```

Tasks:

```text
1. On MacBook: inspect `claude --version`, `claude remote-control --help`, `claude --remote-control`.
2. Test Claude Code worktree spawn mode.
3. Test Claude Code remote session from mobile.
4. On MacBook/Windows: inspect `codex --version`, `codex --help`, `codex remote-control --help`.
5. Test Codex local session and remote-control if available in your installed version.
6. Create a small Python PTY wrapper that starts a CLI and streams output to a browser.
7. Create a git worktree per test session.
8. Capture diff after agent finishes.
9. Store transcript and diff in local SQLite/Postgres.
10. Document all incompatibilities.
```

Exit criteria:

```text
From a browser, you can start a local session, watch output, stop it, and collect a diff.
```

### Phase 1 — private single-user dashboard

Goal:

```text
One user, one backend, one host daemon, two runtimes.
```

Deliverables:

```text
Django backend
PostgreSQL models
host registration
runtime probe
Claude adapter
Codex adapter
session list
session detail
live event stream
stop button
artifact collection
```

### Phase 2 — approvals and policies

Goal:

```text
Agents cannot perform risky actions silently.
```

Deliverables:

```text
PolicyProfile model
ApprovalRequest model
command classifier
blocked commands
push/open-PR approval
mobile approval cards
audit log
```

### Phase 3 — GitHub review workflow

Goal:

```text
Every useful session can become a reviewable branch/PR.
```

Deliverables:

```text
branch naming convention
PR creation
GitHub status sync
Continue checks
PR link artifact
revision loop
```

### Phase 4 — native/custom runtimes

Goal:

```text
Move beyond wrapper mode where useful.
```

Deliverables:

```text
OpenHands adapter
Cline SDK evaluation
Aider adapter
custom shell/script adapter
Obsidian context loader
Langfuse/promptfoo hooks
```

---

## 19. Recommended branch/worktree policy

```text
main/master is always read-only for agents.
Each session gets a branch:
agent/YYYY-MM-DD/runtime/project-short-task

Each session gets a worktree:
.worktrees/{session_id}-{slug}/

Each runtime only sees that worktree.
Generated files stay inside that worktree.
Push requires explicit approval.
PR creation requires explicit approval.
Merge is manual only.
```

---

## 20. How to use this immediately without building everything

You can already approximate the workflow today:

```text
1. Put MacBook + Windows machine + phone on Tailscale.
2. Use Tailscale SSH or a mobile SSH client for emergency terminal access.
3. Use Claude Code Remote Control for Claude sessions.
4. Use Codex app/CLI/remote-control where available.
5. Use Git worktrees manually per task.
6. Use GitHub draft PRs as review artifacts.
7. Use Continue checks on PRs.
8. Use an Obsidian note per agent session for manual memory.
```

This is not the final product, but it proves the operating model before you write the full platform.

---

## 21. Build / buy / integrate decision

### Build yourself

Build:

```text
Project/host/runtime registry
Session metadata
Policy engine
Approval inbox
Audit trail
Mobile dashboard
Host daemon
Artifact collection
```

### Integrate

Integrate:

```text
Claude Code for premium local reasoning
Codex for OpenAI-native coding/mobile workflows
GitHub Agent HQ for public/internal PR tasks
Cline SDK/Kanban if useful
OpenHands SDK for custom agents
Aider for simple local edits
Continue for AI PR checks
Tailscale for private access
n8n/Windmill for notifications/ops
```

### Do not build yet

Do not build initially:

```text
custom model gateway;
custom IDE;
custom agent reasoning framework;
full browser computer-use stack;
complex marketplace;
enterprise SaaS tenancy;
Office/Excel agent subsystem.
```

---

## 22. Technical risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Runtime CLI output is unstructured | Hard to detect approvals/events reliably | Start with log streaming + artifact capture; add structured adapters where APIs mature |
| Claude/Codex remote-control surfaces are provider-owned | You cannot fully embed or manipulate them | Treat them as external runtime surfaces; track metadata externally |
| Codex changes quickly | Adapter can break | Feature-detect commands and versions |
| Agents can damage repos | Core safety risk | Worktrees, branch isolation, command approvals, no-main guard |
| Secrets leak into logs | High risk | redaction, restricted directories, `.env` blocklist, no raw logs to external tools |
| Mobile approvals can be careless | Human risk | risk tiers, preview, no destructive mobile approval by default |
| GitHub cloud agents break sovereignty assumptions | Strategic risk | sensitivity policy blocks cloud agents by default for confidential work |
| Overbuilding orchestration | Slows MVP | start with two runtimes and one user |

---

## 23. Best first implementation target

The best first real version is:

```text
Private single-user Agent Command Center
running on your tailnet
with one backend and one host daemon
supporting Claude Code and Codex
with worktree isolation
live logs
stop button
artifact capture
manual approvals
branch/PR review loop.
```

This is enough to be useful for your own workflow and small enough to build.

---

## 24. Strong product thesis

The market is moving from:

```text
I ask one AI assistant in one interface.
```

toward:

```text
I supervise many agents working across many environments.
```

But the missing layer is not intelligence. The missing layer is **operations**:

```text
Where is the agent running?
Which repo is it touching?
Which branch?
Which model?
Which files?
Which commands?
Who approved it?
What changed?
Can I stop it?
Can I review it?
Can I trust the audit trail?
```

That is the product.

---

## 25. Suggested agent launch prompt

Use this with Claude Code or Codex to start a serious implementation plan:

```text
We are building Agent Command Center: a private local-first control plane for supervising AI coding agents across machines, projects, and runtimes.

Research conclusion:
- Do not build a new coding agent first.
- Build the operational control layer above Claude Code, Codex, Cline, OpenHands, Aider, and GitHub agents.
- MVP must support one user, one backend, one host daemon, Claude Code adapter, Codex adapter, git worktree isolation, live event streaming, stop button, artifacts, and approval requests.

Stack:
- Django + DRF + PostgreSQL + Redis/Celery + Channels
- Python host-agent daemon
- Next.js PWA frontend
- Tailscale-only private access

Constraints:
- no public exposure;
- no direct edits on main;
- no automatic merge;
- no plaintext credentials;
- no cloud agents for confidential repos by default;
- feature-detect Claude/Codex capabilities;
- keep runtime adapters modular;
- start simple and prove process supervision first.

Deliverables:
1. Detailed repo structure.
2. Django data models.
3. Host daemon architecture.
4. Runtime adapter interface.
5. Claude Code adapter plan.
6. Codex adapter plan.
7. Session lifecycle.
8. Worktree/branch policy.
9. Security model.
10. First 20 implementation tasks with acceptance criteria.
```

---

## 26. Source review

Primary / official sources reviewed for this V2:

1. Claude Code Remote Control documentation — https://docs.anthropic.com/en/docs/claude-code/remote-control
2. Claude Code IAM/authentication documentation — https://docs.anthropic.com/en/docs/claude-code/iam
3. OpenAI Codex GitHub repository — https://github.com/openai/codex
4. OpenAI Codex releases — https://github.com/openai/codex/releases
5. OpenAI Codex changelog — https://developers.openai.com/codex/changelog
6. GitHub Agent HQ announcement — https://github.blog/news-insights/company-news/pick-your-agent-use-claude-and-codex-on-agent-hq/
7. GitHub Copilot cloud agent docs — https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent
8. GitHub third-party agents docs — https://docs.github.com/en/copilot/concepts/agents/about-third-party-agents
9. OpenHands Software Agent SDK repository — https://github.com/OpenHands/software-agent-sdk
10. Cline overview docs — https://docs.cline.bot/cline-overview
11. Cline SDK page — https://cline.bot/sdk
12. Aider documentation — https://aider.chat/docs/
13. Continue docs — https://docs.continue.dev/
14. Tailscale Serve docs — https://tailscale.com/docs/features/tailscale-serve
15. Tailscale ACL docs — https://tailscale.com/docs/features/access-control/acls
16. LangGraph page — https://www.langchain.com/langgraph
17. CrewAI introduction — https://docs.crewai.com/en/introduction
18. AutoGen docs — https://microsoft.github.io/autogen/stable/
19. n8n AI workflow docs — https://docs.n8n.io/advanced-ai/intro-tutorial/

---

## 27. Final recommendation

Build the **Agent Command Center** as an operator layer, not as another agent.

The winning architecture is:

```text
Tailscale private access
+ Django control plane
+ PostgreSQL audit/session database
+ Python host daemon
+ runtime adapters
+ git worktree isolation
+ mobile approval inbox
+ artifact capture
+ GitHub PR review loop.
```

Start with:

```text
Claude Code adapter
Codex adapter
one host
a few projects
manual approvals
worktree isolation
log streaming
artifact capture
```

Then add:

```text
Cline/OpenHands for native programmable agents
Aider for lightweight local edits
GitHub Agent HQ for cloud PR workers
Continue for AI quality checks
Obsidian/Langfuse/promptfoo for memory/evals/telemetry.
```

The strategic positioning is strong:

> The future is not one assistant. The future is many specialized agents. The serious product is the command center that lets you control, review, and trust them.
