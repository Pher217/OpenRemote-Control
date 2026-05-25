---
type: spec
project: Ideas
idea: Agent Command Center
version: v3-sharpened
date: 2026-05-25
status: draft — awaiting A/B/C decision
predecessor: agent-command-center-v2-archived
research: 2026-05-25-agent-command-center-market-review
---

# Agent Command Center — V3 (Sharpened, Post-Market-Scan)

> **Read the research first:** [[2026-05-25-agent-command-center-market-review]]. The V2 spec ([[agent-command-center-v2-archived]]) was drafted assuming this space was open. May 2026 scan shows ~10 competitors. This V3 spec exists only to define Path B from the research: the narrow defensible slice.
>
> If Path A or C is chosen, this spec is dead.

## 0. Sharpened thesis

V2 said: *"build the control plane that supervises Claude/Codex/Cline/OpenHands."*
V3 says: *"build the policy + approval + audit layer that wraps an existing runtime supervisor (Remotelab or agentsmesh), targeted at Swiss-sovereignty multi-host workflows."*

The runtime layer is no longer the moat. The moat is:

```
(self-hosted) × (multi-host) × (sensitivity policy) × (mobile approval inbox) × (queryable audit)
```

Nothing currently ships all five together.

## 1. What V3 is and is not

### Is

- A **policy + approval + audit overlay** on top of an existing runtime supervisor.
- A **multi-host registry** that knows which of your machines may run which projects.
- A **mobile-first approval PWA** (push-driven, risk-tiered, not a terminal viewer).
- A **single queryable Postgres audit log** across heterogeneous runtimes and hosts.
- **Local-first**: backend on a private VPS or a designated home server, accessible only over Tailscale.

### Is not

- Not a new runtime adapter framework. Wrap Remotelab/agentsmesh; do not rebuild them.
- Not a worktree/branch manager. Remotelab + git already do it; don't duplicate.
- Not a multi-agent orchestrator. One human, one approval queue.
- Not a public SaaS. Single tenant, single user, Tailscale-only.
- Not an IDE, not a model gateway, not a workflow builder.

## 2. Build / wrap / integrate decision

| Concern | Decision | Reason |
|---|---|---|
| Process supervision (PTY, stream stdout, lifecycle) | **Wrap Remotelab** | Already mature, multi-runtime, mobile-friendly browser. |
| Git worktree per session | **Use git directly** | Trivial. Don't add a layer. |
| Runtime adapters for Claude/Codex/Cline | **Inherited from Remotelab** | Skip entirely. |
| Host registry (which Mac, which PC, which VPS) | **Build** | Remotelab is single-host. This is the multi-host moat. |
| Project sensitivity policy | **Build** | Nobody ships this. Schatzi/Matisa actually need it. |
| Approval inbox (mobile-first, push, risk tiers) | **Build** | Remotelab is read-mostly; approval-first is a different UX. |
| Audit log (queryable Postgres, cross-runtime) | **Build** | Nobody ships this. Compliance requirement. |
| GitHub PR review loop | **Use GitHub directly + Continue checks** | Don't rebuild. |
| Cloud agent integration (Agent HQ, Codex Web) | **Defer to Phase 2** | Not needed for sovereignty case. |
| LLM gateway, custom agent runtime, IDE | **Don't build** | Out of scope. |

## 3. Minimum viable scope (4 weeks, 1 person)

### Week 1 — host + project registry

- Django + Postgres + DRF
- `Host` model: tailnet identity, OS, allowed roots, capabilities, status
- `Project` model: slug, repo_url, local_paths-per-host, sensitivity_level, allowed_hosts, allowed_runtimes
- `PolicyProfile` model linked to sensitivity_level
- Admin UI to seed your real hosts + projects
- Host-side check-in script (cron + curl) that reports `host alive + Remotelab port + runtimes detected`

**Exit:** dashboard lists your 3 hosts (MacBook, Windows workstation, VPS), 5 projects, marks confidential ones in red.

### Week 2 — session intent + approval gate (before runtime starts)

- `SessionIntent` model: project, host, runtime, prompt, requested_by, status
- API: `POST /api/session-intents/` — validates project sensitivity vs. host vs. runtime before allowing launch
- Cloud-agent block for confidential projects (rejected at intent time, not at runtime)
- Mobile PWA: list of pending intents → approve → on approve, backend opens corresponding Remotelab session via Remotelab's own API

**Exit:** from phone, you submit a session intent → policy engine rejects "cloud-agent on confidential" → submit a local-agent intent → approve → Remotelab session opens on the chosen host.

### Week 3 — audit + risk-tiered approvals during session

- `AuditEvent` model (append-only Postgres table): timestamp, host, project, runtime, session_id, actor, event_type, payload_json, redacted_text
- Tail Remotelab's session logs → normalize → write to AuditEvent
- Command classifier (regex + allowlist + denylist) on shell commands extracted from logs → emit `ApprovalRequest` rows for High/Destructive tiers
- Mobile PWA: push notification on pending approval → approve once / reject / approve-similar-this-session
- Secrets redaction at audit ingest (env var names, known token patterns)

**Exit:** from phone, mid-session, you receive a push when the agent tries `git push` on a confidential repo → approve → command runs. Audit DB has the full event trail, queryable in admin.

### Week 4 — artifact capture + PR loop + hardening

- `Artifact` model: diff, transcript, PR link, test output
- Post-session collector: read git diff from worktree, dump transcript from Remotelab, attach PR URL if pushed
- GitHub PR creation via `gh` CLI on approved push
- Tailscale Serve for backend; Tailscale ACLs for least privilege
- Backup: Postgres dump to encrypted S3-compatible storage

**Exit:** end-to-end loop works on your real workflow for 1 week. If it doesn't, kill the project.

## 4. Data model (minimal — only what V2 cut)

V2 had 10 models. V3 keeps 6 (drops the ones already owned by Remotelab + git).

```text
Host                  # multi-host registry — the moat
Project               # sensitivity-aware project registry — the moat
PolicyProfile         # sensitivity → policy rules — the moat
SessionIntent         # gate before launching a runtime — new
AuditEvent            # append-only cross-runtime audit log — the moat
ApprovalRequest       # mid-session risk-tiered approvals — the moat
```

Dropped from V2: `Runtime` (queryable via Remotelab), `AgentSession` (Remotelab owns it; we store only the ref), `AgentEvent` (folded into AuditEvent), `Artifact` (kept but minimal), `AgentTask` (out of scope).

## 5. Architecture diagram

```text
           ┌──────────────────────────────┐
           │  Mobile PWA (approval inbox) │
           └──────────────┬───────────────┘
                          │ Tailscale Serve / HTTPS
                          ▼
           ┌──────────────────────────────┐
           │   Django backend             │
           │   - HostRegistry             │
           │   - ProjectRegistry          │
           │   - PolicyEngine             │
           │   - ApprovalQueue            │
           │   - AuditLog (Postgres)      │
           └─────┬────────────────┬───────┘
                 │                │
        Tailscale│        Postgres│
                 ▼                ▼
        ┌─────────────────┐  ┌────────────┐
        │ Remotelab on    │  │ S3 backup  │
        │ each host       │  │            │
        │ (MacBook / PC / │  └────────────┘
        │  VPS) — runtime │
        │ supervision     │
        └─────────────────┘
              │
              ▼
        Claude Code / Codex / Cline (managed by Remotelab)
```

## 6. Hard rules (kept from V2, unchanged)

- No direct edits on `main`.
- No automatic merge.
- No public dashboard exposure (Tailscale Serve only).
- No plaintext provider credentials.
- No cloud agent for projects marked `confidential` or `regulated`.
- No agent access to filesystem roots outside project allowlist.
- Audit log append-only; deletion only via admin script with reason.

## 7. Risks (V3-specific)

| Risk | Mitigation |
|---|---|
| Remotelab API isn't stable enough to wrap | Spike Week 0: build a 100-line Python wrapper against Remotelab's current API; if it leaks, fall back to driving Remotelab via headless browser or fork it. |
| Microsoft Agent 365 extends to GitHub code agents in next release | Acceptable — Agent 365 is cloud and M365-bound; sovereignty case stays. |
| Remotelab itself ships policy + audit in Q3 2026 | Real risk. Re-evaluate at end of Week 2; if Remotelab roadmap has it, kill project (decision A). |
| Audit DB grows fast | Partition by month; retention policy in PolicyProfile. |
| User skips mobile approvals out of friction | Auto-approve rules per project + per runtime + per risk tier; defaults strict, can be relaxed per project. |

## 8. Kill criteria (Week 2 checkpoint)

If at end of Week 2 any of these is true → stop, write a postmortem, choose Path A:

- Remotelab integration takes >1 week on its own (means the API is wrong shape).
- Policy engine still feels like a toy on real workflows.
- You haven't used the mobile PWA from your phone in anger by Day 10.
- A competitor announces the same triple intersection.

## 9. What V3 still does not solve

- **OfficeLabs document agents** — control-plane pattern probably applies, but not in scope for V3.
- **Multi-user, team workflows** — explicit non-goal; single user.
- **Cloud-agent governance** (Agent HQ, Codex Web) — Phase 2 if V3 ships and traction exists.
- **Cline Kanban-style parallel multi-agent** — explicit non-goal; one approval queue, one human.

## 10. Decision required from Philippe

Pick one:

- **A. Abandon.** Use Remotelab + manual git worktrees for 6 months. Re-scan competitors Q4 2026.
- **B. Build V3 as scoped (this spec).** 4 weeks, 1 person, kill at Week 2 if checkpoint fails.
- **C. Pivot to OfficeLabs document-agent control plane.** Same architecture, different vertical, no direct competitor.

Default if no answer in 1 week: **A**. The market is moving too fast to build speculatively.

## Backlinks
- [[Ideas — Project Overview]]
- [[Ideas - index]]
- [[2026-05-25-agent-command-center-market-review]]
- [[agent-command-center-v2-archived]]
