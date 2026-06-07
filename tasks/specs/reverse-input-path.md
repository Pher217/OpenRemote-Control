# Spec — Reverse path: Telegram → live session input (`orc run` + PTY injection)

> Status: draft for approval · Author: orchestrated (Opus) from a Sonnet scoping pass · 2026-06-07
> Origin: live multi-machine test — replying in a Telegram session topic did nothing.

## Problem & goal

Today the system is **one-way**: the observer tails a coding session's JSONL and surfaces it into a Telegram forum topic. When the user replies inside that topic, the message is dropped — there is no path back into the running session.

**Goal:** let the user reply in a Telegram topic and have that text injected as input into the corresponding live coding session, **safely** (irreversible keystrokes must be gated), across machines.

## The hard constraint (read first)

Input injection is **only feasible for sessions orc launches under its own PTY** (`orc run`, `Thread.RuntimeModeChoices.PTY`). For **observed** sessions (Claude Code/Codex/Gemini started externally by the user), the daemon holds no handle to the process's terminal and cannot type into it without fragile, platform-specific OS hacks (`/proc/<pid>/fd/0`, `expect`). 

**Consequence:** a session must be **started through orc** to be driveable from Telegram. Observed sessions remain read-only and must reply with a clear "this session is read-only — start it with `orc run` to send input" message. This is a product decision baked into the spec, not a limitation to be worked around.

## What already exists (reuse, don't rebuild)

- **Downstream channel backend→host**: `HostDaemonConsumer.host_command` (`backend/apps/hostlink/consumers.py:202`) + per-host group `host_{host.id}` (set at `connect()`, ~`consumers.py:86`). `channel_layer.group_send("host_{id}", {...})` already reaches a connected daemon.
- **PTY write + safety gate**: `host-agent/agent_host/pty_session.py::PtySession.send_keys()` (~lines 145–214) classifies input via `input_policy.classify_input()` and enforces SAFE / REVIEW / DANGEROUS. Fully implemented, **never called**.
- **Inbound-answer precedent**: the connector `ask_human`/`request_approval` flow (`backend/apps/connectors/*`, `backend/apps/prompts/*`, `resolve_prompt`) — a working pattern for a Telegram reply/button resolving a pending backend request. Reuse its `Prompt`/nonce model for approval gating.
- **One-way topic mapping**: `Thread.metadata["telegram_topic_id"]` written by `delivery.py::_save_topic_id()` (~lines 32–35).

## What's missing (the gaps)

1. Bot loop discards `message_thread_id` (`backend/apps/telegram/management/commands/run_telegram_bot.py:55`) and gates on `TELEGRAM_ALLOWED_CHAT_IDS`, which the forum group id fails (`service.py:60`).
2. No reverse lookup `(forum_chat_id, topic_id) → Thread`.
3. Host daemon ws is **send-only** — `wsclient.run_sender` has no `recv` loop; inbound frames are never read.
4. `PtySession` has no caller and no link to the ws receive path.
5. `orc run` (launch a session under a daemon-owned tmux PTY) does not exist.

## Architecture

```
Telegram topic reply
   │  (message_thread_id + from.id)
   ▼
run_telegram_bot ──▶ reverse lookup (forum_chat_id, topic_id) ─▶ Thread (+host, +tmux_session_name)
   │                                   │
   │   reject if observed / not authed │
   ▼                                   ▼
Approval Prompt (reuse connectors/prompts)  ── user taps Allow ──┐
                                                                 ▼
              channel_layer.group_send("host_{host.id}", {type:host_command, command:"pty.inject", ...})
                                                                 ▼
              host daemon ws RECV loop ─▶ inject handler ─▶ PtySession.send_keys(name, text, approved=True)
                                                                 ▼
                                                    input_policy gate ─▶ tmux pane stdin
```

## Implementation phases (each ships with tests)

### Phase 1 — Telegram inbound routing + reverse lookup (backend)
- Read `message.get("message_thread_id")` in the bot loop; pass it to a new forum-reply handler.
- Auth: accept forum-group messages only when `message["from"]["id"]` ∈ `TELEGRAM_ALLOWED_CHAT_IDS` (mirror `handle_callback_query` at `service.py:102`) AND `chat.id == TELEGRAM_FORUM_CHAT_ID`.
- Reverse lookup: `Thread.objects.select_related("host").filter(metadata__telegram_topic_id=topic_id, metadata__telegram_forum_chat_id=forum_id).first()`. Store `telegram_forum_chat_id` in metadata alongside `telegram_topic_id` (update `delivery.py::_save_topic_id`) so the lookup is scoped (topic ids are only unique per group, and reusable after deletion).
- If the matched thread is OBSERVED (or has no host / no `tmux_session_name`), reply read-only and stop.
- **Tests:** topic reply resolves to the right thread; wrong-forum/unauthed user rejected; observed session → read-only reply; topic-id collision across forums disambiguated.

### Phase 2 — Host daemon becomes bidirectional (host-agent)
- Add a `recv` task inside `async with connect(url) as ws` in `wsclient.py`, run concurrently with the send loop (`asyncio.gather`), so a recv failure doesn't kill the sender and vice-versa. Dispatch inbound `host_command` frames to a handler callback.
- **Tests:** inbound frame is dispatched; recv error triggers reconnect (re-sign) without losing the send loop; malformed frame ignored.

### Phase 3 — `orc run` (host-agent + backend)
- `orc-host run <cmd>` launches the command in a daemon-owned tmux session via `PtySession.start()`; registers a Thread with `runtime_mode=PTY`, `metadata.tmux_session_name`, and streams its output through the existing observe pipeline.
- **Tests:** run creates a tmux session + PTY thread; output streams; session name persisted.

### Phase 4 — Inject pipeline (backend + host-agent)
- Backend `apps/hostlink/service.py::send_pty_input(thread, text)` → `group_send("host_{host.id}", {type:"host_command", command:"pty.inject", session_name, text, approved})`.
- Host inject handler → `PtySession.send_keys(session_name, text, approved=approved)`.
- **Tests:** backend push reaches the handler; handler calls send_keys with the right args; DANGEROUS input blocked even when approved=False; non-existent session handled.

### Phase 5 — Approval gating (the safety core)
- Every Telegram-originated injection creates an approval `Prompt` (reuse `request_approval`) showing the text; inject only on Allow; deny/timeout = no-op (fail-closed, matching the connector contract).
- Input policy is the **second** gate on the host (defense in depth).
- **Tests:** inject happens only after Allow; deny/timeout injects nothing; DANGEROUS classification blocks regardless.

## Security & correctness (non-negotiable for v1)
- **Approval-first**: gate *every* injected keystroke through an explicit Allow, even SAFE-classified, in v1.
- **From-user auth** on the forum path (`message["from"]["id"]` ∈ allowed set) — not just chat id.
- **Topic scoping**: query on `(telegram_forum_chat_id, telegram_topic_id)`, never topic id alone.
- **Dedup**: persist last-processed `update_id` so a bot restart can't replay a reply into the PTY.
- **Observed = read-only**: explicit user feedback, never a silent drop.

## Open questions
1. Approval UX: a second tap per message is safe but heavy — offer a per-session "trust for N minutes" after the first Allow? (v2.)
2. Does `orc run` wrap an interactive Claude Code, or only non-interactive commands first? Determines how `send_keys` framing (enter/submit) works per runtime.
3. Multi-user forums are out of scope (single trusted operator assumed).

## Effort (rough)
Phase 1 ~½ day · Phase 2 ~½ day · Phase 3 (`orc run`) ~1–2 days (largest; new surface) · Phase 4 ~½ day · Phase 5 ~½ day. Suggest one PR per phase; Phase 3 may split.

## Implementation map (anchors)
- `backend/apps/telegram/management/commands/run_telegram_bot.py:55` — extract `message_thread_id`.
- `backend/apps/telegram/service.py:60` — forum-reply auth branch + reverse lookup.
- `backend/apps/observe/delivery.py:32` — also persist `telegram_forum_chat_id` in metadata.
- `backend/apps/hostlink/consumers.py:202` — existing `host_command` downstream handler (reuse).
- `host-agent/agent_host/wsclient.py` (~132, inside `async with connect`) — add recv task.
- `host-agent/agent_host/pty_session.py:145` — `send_keys` (reuse, the inject target).
- `host-agent/agent_host/input_policy.py` — SAFE/REVIEW/DANGEROUS classifier (the host gate).
