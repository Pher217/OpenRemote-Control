"""
wsclient.py — Authenticated bidirectional WebSocket client for the host daemon.

connect_url()
    Builds the signed WebSocket URL with a fresh timestamp and nonce.

run_sender()
    Async loop that connects (with automatic reconnect), drains the offline
    queue, then runs a send loop and a receive loop concurrently via
    asyncio.gather.  A failure in either loop tears both down so the outer
    reconnect logic re-signs and retries.  The *connect*, *stop*, and
    *on_command* parameters are injectable for testing.

handle_host_command()
    Default handler for inbound host_command frames from the backend.
    Dispatches on frame["command"]: "ping" is acknowledged, unknown commands
    are logged and ignored.  "pty.inject" is reserved for Phase 4.
    "tail.start"/"tail.stop" manage a scoped TranscriptTail per claude_session_id
    (see agent_host.transcript_tail) so editor-typed turns can be mirrored to
    the chat connector without scanning the whole transcript directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import websockets

from agent_host.config import HostConfig, load
from agent_host.queue import OfflineQueue
from agent_host.signing import sign
from agent_host.transcript_tail import TranscriptTail

log = logging.getLogger(__name__)

# Per-claude-session serialization locks for headless.prompt so two prompts to
# the same session cannot overlap (dict is module-level; lock created on first use).
_headless_locks: dict[str, asyncio.Lock] = {}

# Active scoped transcript tails, keyed by claude_session_id. Populated by
# "tail.start" / "tail.stop" host commands; consulted by headless.prompt to
# suppress the tail during a live-streamed drive (two-writer dedup).
_transcript_tails: dict[str, TranscriptTail] = {}

# Persistent interactive engines (ORC_HEADLESS_ENGINE=interactive), keyed by
# claude_session_id. One long-lived `claude -p` stream-json process each —
# spawned on first prompt, stopped on daemon shutdown or turn timeout.
_engines: dict = {}


async def _interactive_turn(claude_session_id, cwd, text, on_event, loop, started=False) -> bool:
    """Run one turn on the persistent per-session engine; return is_error.

    Called under the per-session headless lock, so turns are serialized here
    and the engine's internal FIFO is only a safety net. Callbacks are rebound
    per turn — safe because the lock guarantees one turn at a time.
    """
    from agent_host.interactive_engine import InteractiveEngine  # noqa: PLC0415

    done = asyncio.Event()
    err = [True]

    def _turn_complete(is_err: bool) -> None:
        err[0] = is_err
        loop.call_soon_threadsafe(done.set)

    engine = _engines.get(claude_session_id)
    if engine is None:
        engine = InteractiveEngine(
            claude_session_id, cwd, on_event, _turn_complete, started=started,
        )
        _engines[claude_session_id] = engine
    else:
        engine.on_event = on_event
        engine.on_turn_complete = _turn_complete
    # send() does blocking pipe IO (and possibly a spawn) — keep it off the
    # event loop so a stalled child can never wedge the daemon's heartbeat.
    await asyncio.to_thread(engine.send, text)
    try:
        await asyncio.wait_for(done.wait(), timeout=600)
    except TimeoutError:
        log.warning(
            "interactive engine: turn timeout — recycling engine for %s",
            claude_session_id,
        )
        _engines.pop(claude_session_id, None)
        await asyncio.to_thread(engine.stop)
        return True
    return err[0]


# Per-session Codex drive engines (provider="codex"), keyed by thread_id.
# Codex hides its session id from the MCP subprocess, so the engine spawns a
# fresh `codex exec` session on the first turn and captures its thread_id to
# resume subsequent turns. Keyed by thread_id (stable across the session).
_codex_engines: dict = {}


async def _codex_turn(thread_id, cwd, text, on_event, loop, initial_session_id="") -> bool:
    """Run one turn on the per-session Codex engine; return is_error.

    Called under the per-session headless lock (turns serialized). The engine
    itself is per-turn `codex exec [resume]`, so this only holds one Codex
    session's continuity via the captured thread_id. ``initial_session_id`` (the
    bind) is applied ONLY when the engine is first created — the operator's
    discovered session, resumed on turn 1; later turns follow the fork chain.
    """
    from agent_host.codex_engine import CodexEngine  # noqa: PLC0415

    done = asyncio.Event()
    err = [True]

    def _turn_complete(is_err: bool) -> None:
        err[0] = is_err
        loop.call_soon_threadsafe(done.set)

    engine = _codex_engines.get(thread_id)
    if engine is None:
        engine = CodexEngine(
            cwd, on_event, _turn_complete, session_id=(initial_session_id or None)
        )
        _codex_engines[thread_id] = engine
    else:
        engine.on_event = on_event
        engine.on_turn_complete = _turn_complete
    # send() spawns a subprocess (blocking) — keep it off the event loop.
    await asyncio.to_thread(engine.send, text)
    try:
        await asyncio.wait_for(done.wait(), timeout=600)
    except TimeoutError:
        log.warning("codex engine: turn timeout — recycling engine for thread %s", thread_id)
        _codex_engines.pop(thread_id, None)
        await asyncio.to_thread(engine.stop)
        return True
    return err[0]

# Events whose JSON encoding exceeds this byte threshold are dropped rather than
# sent — a single oversized frame will cause the server to close the connection
# and would poison the offline queue indefinitely if retried.
MAX_EVENT_BYTES = 1_000_000

# Heartbeat: the daemon sends a host_heartbeat up to the backend every
# HEARTBEAT_INTERVAL seconds; the backend echoes a `ping` host_command back
# through the channel-layer group. If no ping returns within HEARTBEAT_TIMEOUT
# seconds, the channel path is presumed dead and the connection is torn down so
# the outer loop reconnects with a fresh backend consumer.
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 90.0


def connect_url(backend_url: str, cfg: HostConfig) -> str:
    """Build a signed WebSocket URL for the given config.

    URL scheme: http → ws, https → wss.
    Query parameters: token, ts (unix seconds), nonce (uuid4 hex), signature.

    A fresh ts and nonce are generated on every call so the URL cannot be
    replayed.

    Parameters
    ----------
    backend_url:
        Base URL, e.g. "https://orc.example.com".
    cfg:
        HostConfig with host_id and token.

    Returns
    -------
    str
        Fully-qualified signed WebSocket URL.
    """
    base = backend_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = base

    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = sign(cfg.token, cfg.host_id, ts, nonce)

    url = (
        f"{ws_base}/ws/hosts/{cfg.host_id}/"
        f"?token={cfg.token}&ts={ts}&nonce={nonce}&signature={sig}"
    )
    return url


def handle_host_command(
    frame: dict,
    incoming_queue: asyncio.Queue | None = None,
    *,
    owns_session: Callable[[str], bool] | None = None,
    _session_start_task_factory: Callable | None = None,
) -> None:
    """Default handler for inbound host_command frames from the backend.

    Dispatches on frame["command"]:
    - "ping": logs receipt and, if an outbound queue is available, enqueues
      a host_command_ack event so the round-trip is observable.
    - "pty.inject": reserved for Phase 4 (PTY keystroke injection).
    - "session.kill": kill a named tmux session (Fleet F3 /stop).
    - "session.start": launch a new PTY session and stream output (Fleet F3 /run).
    - anything else: logged as unknown and ignored.

    Parameters
    ----------
    frame:
        Parsed JSON frame received from the backend.  Must contain at least
        ``{"type": "host_command", "command": "<name>"}``.
    incoming_queue:
        The internal asyncio.Queue used by run_sender to ship outbound events.
        When provided, a "ping" will enqueue an ack back to the backend.
    _session_start_task_factory:
        Injectable for testing the session.start branch.  Receives the ws
        reference (None in the sync handler context) and the coroutine;
        defaults to creating an asyncio task on the running loop.
    """
    command = frame.get("command", "")
    if command == "ping":
        log.info("host_command: ping received")
        if incoming_queue is not None:
            # Enqueue an ack — best-effort, non-blocking (queue is unbounded).
            try:
                incoming_queue.put_nowait({"type": "host_command_ack", "command": "ping"})
            except Exception:
                log.debug("host_command: could not enqueue ping ack")
    elif command == "pty.inject":
        session_name = frame.get("session_name", "")
        text = frame.get("text", "")
        approved = bool(frame.get("approved", False))
        if not session_name or not text:
            log.warning(
                "host_command: pty.inject missing session_name or text — ignoring"
            )
            return
        # Ownership guard: pty.inject is broadcast to every host ws connection in
        # the host group (the daemon + each `orc run`). Only the process that
        # started the session may inject — otherwise the keystrokes are duplicated
        # N times. When owns_session is None (e.g. unit tests), no filtering.
        if owns_session is not None and not owns_session(session_name):
            log.debug(
                "host_command: pty.inject for session %r not started here — skipping",
                session_name,
            )
            return
        try:
            from agent_host.pty_session import PtySession  # noqa: PLC0415

            PtySession().send_keys(session_name, text, approved=approved)
            log.info(
                "host_command: pty.inject delivered to session %r (%d chars)",
                session_name,
                len(text),
            )
        except PermissionError as exc:
            log.error("host_command: pty.inject blocked by policy: %s", exc)
        except KeyError as exc:
            log.error("host_command: pty.inject unknown session %r: %s", session_name, exc)
        except Exception:
            log.exception("host_command: pty.inject raised unexpectedly — recv loop continues")
    elif command == "session.kill":
        # Fleet F3 /stop: kill a named tmux session.  No approval required —
        # stopping is a kill-switch; it must always be reachable for an
        # authenticated operator.  Identity gate is enforced on the backend.
        session_name = frame.get("session_name", "")
        if not session_name:
            log.warning("host_command: session.kill missing session_name — ignoring")
            return
        try:
            from agent_host.pty_session import PtySession  # noqa: PLC0415

            PtySession().kill(session_name)
            log.info("host_command: session.kill terminated session %r", session_name)
        except Exception:
            log.exception("host_command: session.kill raised unexpectedly — recv loop continues")
    elif command == "session.start":
        # Fleet F3 /run: start a new PTY session and stream output over the
        # existing WebSocket connection.  The command and cwd are bound in the
        # APPROVAL Prompt on the backend and forwarded here verbatim — never
        # re-read from any Telegram message.
        session_name = frame.get("session_name", "")
        command_str = frame.get("command_str", "")
        cwd = frame.get("cwd") or None
        if not session_name or not command_str:
            log.warning(
                "host_command: session.start missing session_name or command_str — ignoring"
            )
            return
        # session.start is handled asynchronously: we need to launch a tmux
        # session AND run the blocking stream loop on the same WebSocket.  We
        # schedule it as a task on the running event loop so it doesn't block
        # the recv loop (handle_host_command is synchronous).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.error(
                "host_command: session.start called outside running event loop — ignoring"
            )
            return

        async def _start_and_stream() -> None:
            try:
                from agent_host.pty_session import PtySession  # noqa: PLC0415

                pty = PtySession()
                pty.start(session_name, command_str, cwd)
                log.info(
                    "host_command: session.start launched %r command=%r cwd=%r",
                    session_name,
                    command_str,
                    cwd,
                )
            except Exception:
                log.exception(
                    "host_command: session.start failed to launch session %r", session_name
                )
                return

            # The ws reference is not accessible here (handle_host_command is
            # sync and doesn't receive the ws).  We enqueue pty_start and then
            # stream via incoming_queue (the outbound queue).  This mirrors what
            # run_cmd.run_pty does over a dedicated WebSocket, but here we
            # route through the daemon's existing persistent connection via the
            # inline outbound queue.
            if incoming_queue is not None:

                try:
                    incoming_queue.put_nowait({
                        "type": "session.pty_start",
                        "data": {
                            "session_name": session_name,
                            "command": command_str,
                            "cwd": cwd or "",
                        },
                    })
                except Exception:
                    log.exception("host_command: session.start failed to enqueue pty_start")

                # Stream output via the queue (not a raw ws.send call).
                await _stream_via_queue(incoming_queue, pty, session_name)
            else:
                log.warning(
                    "host_command: session.start: no incoming_queue — output will not be streamed"
                )

        if _session_start_task_factory is not None:
            _session_start_task_factory(_start_and_stream())
        else:
            loop.create_task(_start_and_stream())
    elif command == "headless.prompt":
        # Headless Claude relay: run `claude -p` and reply with the result.
        # Blocks up to minutes — must NOT run inline.  Offload to the event
        # loop using the same pattern as session.start above.
        claude_session_id = frame.get("claude_session_id", "")
        text = frame.get("text", "")
        cwd = frame.get("cwd") or ""
        started = bool(frame.get("started", False))
        thread_id = frame.get("thread_id", "")
        provider = (frame.get("provider") or "claude").lower()

        # Codex has no session id at dispatch (Codex hides it from the MCP
        # subprocess) — it is driven by thread_id + cwd. Claude requires its id.
        if not text or (provider != "codex" and not claude_session_id):
            log.warning(
                "host_command: headless.prompt missing text or claude_session_id — ignoring"
            )
            return
        if provider == "codex" and not thread_id:
            log.warning("host_command: headless.prompt codex missing thread_id — ignoring")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.error(
                "host_command: headless.prompt called outside running event loop — ignoring"
            )
            return

        async def _run_headless_prompt() -> None:
            # Serialize per claude_session_id — create lock on first use.
            if claude_session_id not in _headless_locks:
                _headless_locks[claude_session_id] = asyncio.Lock()
            lock = _headless_locks[claude_session_id]
            loop = asyncio.get_running_loop()

            # First reply of this turn carries reset=True so the backend starts a
            # fresh progress digest (one edited message per turn).
            first = [True]

            def _enqueue_reply(reply_text: str, is_err: bool) -> None:
                if incoming_queue is None:
                    log.warning("host_command: headless.prompt: no incoming_queue — reply dropped")
                    return
                try:
                    incoming_queue.put_nowait({
                        "type": "session.headless_reply",
                        "data": {
                            "thread_id": thread_id,
                            "text": reply_text,
                            "is_error": is_err,
                            "reset": first[0],
                        },
                    })
                    first[0] = False
                except Exception:
                    log.exception(
                        "host_command: headless.prompt failed to enqueue reply for thread %r",
                        thread_id,
                    )

            async with lock:
                # Suppress the scoped transcript tail (if any) for this session
                # while we stream this turn live — otherwise the same turn would
                # be forwarded twice once it also lands in the JSONL transcript.
                tail = _transcript_tails.get(claude_session_id)
                if tail is not None:
                    tail.drive_started()
                success = False
                try:
                    # Engine select: ORC_HEADLESS_ENGINE=interactive keeps ONE
                    # persistent `claude -p` stream-json process per session
                    # (no per-turn respawn); =sdk runs via the Agent SDK with
                    # per-tool chat approval. Default streams `claude -p`
                    # step-by-step, one process per turn.
                    engine_mode = os.environ.get("ORC_HEADLESS_ENGINE", "").lower()
                    use_sdk = engine_mode == "sdk"
                    cfg = load() if use_sdk else None
                    if provider == "codex":
                        def on_event(step_text: str) -> None:
                            loop.call_soon_threadsafe(_enqueue_reply, step_text, False)

                        # `started` + a session id means the operator's Codex
                        # session was discovered at dispatch — bind (resume) it.
                        bind_id = claude_session_id if started else ""
                        is_err = await _codex_turn(
                            thread_id, cwd, text, on_event, loop,
                            initial_session_id=bind_id,
                        )
                        if is_err:
                            _enqueue_reply("(codex engine: turn failed)", True)
                        result = {"is_error": is_err}
                    elif engine_mode == "interactive":
                        def on_event(step_text: str) -> None:
                            loop.call_soon_threadsafe(_enqueue_reply, step_text, False)

                        is_err = await _interactive_turn(
                            claude_session_id, cwd, text, on_event, loop,
                            started=started,
                        )
                        if is_err:
                            _enqueue_reply("(interactive engine: turn failed)", True)
                        result = {"is_error": is_err}
                    elif use_sdk and cfg is not None and thread_id:
                        from agent_host.sdk_session import make_approve, run_turn  # noqa: PLC0415

                        approve = make_approve(cfg.backend_url, cfg.token, thread_id)
                        result = await run_turn(
                            text, claude_session_id=claude_session_id, cwd=cwd,
                            started=started, approve=approve,
                        )
                        _enqueue_reply(result["text"], result["is_error"])
                    else:
                        from agent_host.claude_headless import (
                            run_headless_streaming,  # noqa: PLC0415
                        )

                        # Relay each assistant text / tool step live; the backend's
                        # `progress` mode coalesces them into one edited message.
                        def on_event(step_text: str) -> None:
                            loop.call_soon_threadsafe(_enqueue_reply, step_text, False)

                        result = await asyncio.to_thread(
                            run_headless_streaming, text, claude_session_id, cwd, started, on_event
                        )
                        # On failure, nothing useful streamed — surface the error.
                        if result["is_error"]:
                            _enqueue_reply(result["text"], True)
                    success = not result["is_error"]
                finally:
                    if tail is not None:
                        tail.drive_finished(success=success)

        loop.create_task(_run_headless_prompt())
    elif command == "tail.start":
        # Backend requests a scoped tail of exactly one Claude Code transcript
        # so editor-typed turns are mirrored to the chat connector. Never scans
        # a directory — see agent_host.transcript_tail for the poll design.
        thread_id = frame.get("thread_id", "")
        claude_session_id = frame.get("claude_session_id", "")
        cwd = frame.get("cwd") or ""
        provider = frame.get("provider", "")

        if not claude_session_id or not cwd or not thread_id:
            log.warning(
                "host_command: tail.start missing claude_session_id, cwd or thread_id — ignoring"
            )
            return
        if provider != "claude":
            log.warning("host_command: tail.start unsupported provider %r — ignoring", provider)
            return

        existing = _transcript_tails.get(claude_session_id)
        if existing is not None and existing.cwd == cwd:
            return  # Idempotent: already tailing this session at this cwd.

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.error(
                "host_command: tail.start called outside running event loop — ignoring"
            )
            return

        if existing is not None:
            del _transcript_tails[claude_session_id]
            loop.create_task(existing.stop())

        def _emit(event: dict) -> None:
            if incoming_queue is None:
                log.warning("host_command: tail.start: no incoming_queue — event dropped")
                return
            try:
                # data-wrapped like session.headless_reply — the backend
                # consumer reads the payload from content["data"].
                incoming_queue.put_nowait({
                    "type": "session.turn",
                    "data": {
                        "thread_id": thread_id,
                        "claude_session_id": claude_session_id,
                        "role": event["role"],
                        "text": event["text"],
                        "source_event_key": event["source_event_key"],
                    },
                })
            except Exception:
                log.exception(
                    "host_command: tail.start failed to enqueue session.turn for thread %r",
                    thread_id,
                )

        tail = TranscriptTail(claude_session_id, cwd, emit=_emit, loop=loop)
        tail.start()
        _transcript_tails[claude_session_id] = tail
        log.info("host_command: tail.start started for claude_session_id=%r cwd=%r", claude_session_id, cwd)
    elif command == "tail.stop":
        claude_session_id = frame.get("claude_session_id", "")
        if not claude_session_id:
            log.warning("host_command: tail.stop missing claude_session_id — ignoring")
            return
        tail = _transcript_tails.pop(claude_session_id, None)
        if tail is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(tail.stop())
            except RuntimeError:
                log.error(
                    "host_command: tail.stop called outside running event loop — tail removed but not cancelled"
                )
            log.info("host_command: tail.stop stopped tail for claude_session_id=%r", claude_session_id)
    else:
        log.warning("host_command: unknown command %r — ignoring", command)


def _build_reconcile_frame() -> dict | None:
    """Build a session.pty_reconcile frame with live tmux session names.

    Returns None when enumeration fails (no tmux server, libtmux error, etc.).
    Callers MUST skip sending when None is returned — never send an empty list
    caused by an error, as that would falsely mark every session dead.
    """
    try:
        from agent_host.pty_session import PtySession, prune_to_live  # noqa: PLC0415

        names = PtySession().list_live_sessions()
        # Release ownership of sessions that have exited (frees stale names so a
        # reused name can't be injected by both a stale owner and the real one).
        prune_to_live(names)
        return {"type": "session.pty_reconcile", "data": {"session_names": names}}
    except Exception:
        log.debug("pty_reconcile: enumeration failed — skipping frame this cycle")
        return None


async def _stream_via_queue(outbound_queue: asyncio.Queue, pty: Any, session_name: str) -> None:
    """Stream PTY output as queue events (for the daemon's persistent WebSocket).

    This is the daemon-side streaming path for ``session.start``.  Instead of
    calling ``ws.send`` directly (which would require a ws reference), we
    enqueue ``session.pty_output`` and ``session.pty_end`` dicts so the
    daemon's existing ``_sender`` loop forwards them over the live connection.

    The logic mirrors ``pty_stream.stream_pty_output`` but routes through the
    queue instead of a raw ws.
    """
    import asyncio as _asyncio  # noqa: PLC0415 — stdlib

    from agent_host.pty_stream import strip_ansi  # noqa: PLC0415

    sent_lines: int = 0

    def _try_capture() -> str | None:
        try:
            return pty.capture(session_name)
        except KeyError:
            return None

    def _enqueue_diff(raw: str) -> None:
        nonlocal sent_lines
        content = strip_ansi(raw).rstrip()
        lines = content.split("\n") if content else []
        if len(lines) < sent_lines:
            sent_lines = len(lines)
            return
        new_lines = lines[sent_lines:]
        if new_lines:
            new_text = "\n".join(new_lines)
            if new_text.strip():
                try:
                    outbound_queue.put_nowait({
                        "type": "session.pty_output",
                        "data": {
                            "session_name": session_name,
                            "text": new_text,
                        },
                    })
                except Exception:
                    log.warning("_stream_via_queue: could not enqueue pty_output — continuing")
        sent_lines = len(lines)

    while pty.exists(session_name):
        raw = _try_capture()
        if raw is not None:
            _enqueue_diff(raw)
        await _asyncio.sleep(1.0)

    # Final capture
    raw = _try_capture()
    if raw is not None:
        _enqueue_diff(raw)

    # pty_end
    try:
        outbound_queue.put_nowait({
            "type": "session.pty_end",
            "data": {"session_name": session_name},
        })
    except Exception:
        log.warning("_stream_via_queue: could not enqueue pty_end")


async def run_sender(
    cfg: HostConfig,
    queue: OfflineQueue,
    *,
    connect: Any = None,
    stop: asyncio.Event | None = None,
    on_command: Callable[[dict], None] | None = None,
) -> None:
    """Connect to the backend WebSocket and stream queued + incoming events.

    Runs a send loop and a receive loop concurrently via asyncio.gather so the
    connection is fully bidirectional.  A failure in either loop propagates
    through gather (return_exceptions=False), which tears down both coroutines
    and causes the ``async with`` context to exit — triggering the outer
    reconnect logic which re-signs a fresh URL.

    On each connection:
    1. Drain the offline queue (send buffered events to the server).
    2. Run sender and receiver concurrently until a failure or stop signal.
       - Sender: waits on the internal asyncio.Queue for new outbound events.
       - Receiver: waits on ws for inbound frames; dispatches host_command
         frames to on_command; silently ignores malformed JSON and unknown types.

    Events are JSON-encoded before sending.

    Parameters
    ----------
    cfg:
        HostConfig with backend_url, host_id, and token.
    queue:
        OfflineQueue to drain on each (re)connection.
    connect:
        Async context-manager factory for the WebSocket connection.
        Defaults to ``websockets.connect``.  Injectable for tests.
    stop:
        asyncio.Event that, when set, causes the sender to exit cleanly.
        If None, the sender runs until the task is cancelled.
    on_command:
        Callback invoked with each inbound host_command frame.
        Defaults to ``handle_host_command``.  Injectable for tests.
    """
    if connect is None:
        connect = websockets.connect

    if stop is None:
        stop = asyncio.Event()

    if on_command is None:
        # Production daemon: only inject sessions this process started (the daemon
        # owns session.start sessions; `orc run` sessions belong to their own
        # process). This is what prevents duplicate injection across the host group.
        from agent_host.pty_session import was_started_here  # noqa: PLC0415

        def on_command(frame, incoming_queue):
            handle_host_command(frame, incoming_queue, owns_session=was_started_here)

    # Internal queue for new events from the poll loop.
    _incoming: asyncio.Queue[dict] = asyncio.Queue()

    # Attach the incoming queue to the cfg object so daemon.py can push events.
    # This is a simple coupling point; a more complex design could use callbacks.
    cfg._incoming_queue = _incoming  # type: ignore[attr-defined]

    # Re-sign on every connection attempt: each reconnect must carry a fresh
    # ts+nonce. We open ONE connection per signed URL (`async with`) and drive
    # reconnection from this loop — deliberately NOT `async for ws in
    # connect(url)`, whose internal auto-reconnect reuses the same URL (and
    # nonce), which the backend's nonce-replay cache rejects with 4001/403.
    backoff = 1.0
    max_backoff = 30.0
    while not stop.is_set():
        url = connect_url(cfg.backend_url, cfg)
        try:
            async with connect(url) as ws:
                backoff = 1.0  # reset after a successful connection
                # Drain buffered offline events first (iterate manually — drain()
                # is synchronous and cannot await ws.send directly).
                buffered = queue._read_all()
                if buffered:
                    failed: list[dict] = []
                    send_failed = False
                    for event in buffered:
                        encoded = json.dumps(event).encode("utf-8")
                        if len(encoded) > MAX_EVENT_BYTES:
                            log.warning(
                                "Dropping oversized queued event (%d bytes > %d byte limit)",
                                len(encoded),
                                MAX_EVENT_BYTES,
                            )
                            continue  # Skip — never retry, never re-queue.
                        if send_failed:
                            # Keep remaining non-oversized events for next reconnect.
                            failed.append(event)
                            continue
                        try:
                            await ws.send(encoded.decode("utf-8"))
                        except Exception:
                            failed.append(event)
                            send_failed = True
                    if not failed:
                        # All sent — clear queue.
                        if queue._path.exists():
                            queue._path.unlink()
                    else:
                        queue._write_all(failed)

                # ------------------------------------------------------------------
                # Concurrent send + receive loops.
                #
                # asyncio.gather(sender, receiver, return_exceptions=False) means:
                #   • If EITHER coroutine raises, gather immediately cancels the
                #     other and re-raises the exception into the caller.
                #   • The `async with connect(url) as ws:` block then exits
                #     (via the exception propagating out of it), closing the
                #     WebSocket.
                #   • The outer try/except catches the exception; if stop is set,
                #     we return; otherwise we back off and reconnect with a fresh
                #     signed URL.
                #
                # The offline queue is NOT lost on reconnect: the sender re-queues
                # any event that failed to send (via queue.enqueue), which persists
                # it to disk.  The next connection drains that queue first.
                # ------------------------------------------------------------------

                # Send a reconcile frame right after connection is established so
                # the backend can immediately mark dead sessions COMPLETED on
                # (re)connect, before the first heartbeat fires.
                _reconcile = _build_reconcile_frame()
                if _reconcile is not None:
                    try:
                        await ws.send(json.dumps(_reconcile))
                    except Exception:
                        log.debug("pty_reconcile: initial send failed — continuing")

                last_pong = [time.monotonic()]

                async def _sender() -> None:
                    while not stop.is_set():
                        try:
                            event = await asyncio.wait_for(_incoming.get(), timeout=1.0)
                        except TimeoutError:
                            continue
                        try:
                            await ws.send(json.dumps(event))
                        except Exception:
                            # Re-queue the event; the outer loop reconnects + re-signs.
                            queue.enqueue(event)
                            raise  # Propagate so gather tears down the receiver too.

                async def _receiver() -> None:
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        # A recv error other than timeout propagates naturally,
                        # which will cause gather to tear down the sender.
                        try:
                            frame = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            log.debug("ws recv: malformed frame — ignoring")
                            continue
                        if not isinstance(frame, dict):
                            continue
                        if frame.get("type") == "host_command":
                            if frame.get("command") == "ping":
                                # noqa is safe: last_pong is a per-iteration list (line ~432),
                                # and these closures are fully awaited via gather() before the
                                # reconnect loop continues — no stale loop-variable binding.
                                last_pong[0] = time.monotonic()  # noqa: B023
                            try:
                                # Pass the outbound queue so handlers that reply
                                # (headless.prompt, session.start streaming) can
                                # enqueue frames back to the backend. Without it,
                                # they silently drop the reply ("no incoming_queue").
                                on_command(frame, _incoming)
                            except Exception:
                                log.exception("on_command raised — ignoring")
                        # All other types are silently ignored.

                async def _heartbeat() -> None:
                    while not stop.is_set():
                        await asyncio.sleep(HEARTBEAT_INTERVAL)
                        if stop.is_set():
                            return
                        try:
                            await ws.send(json.dumps({"type": "host_heartbeat", "nonce": uuid.uuid4().hex}))
                        except Exception:
                            # Send failed — propagate so gather tears down and reconnects.
                            raise
                        # Send reconcile frame alongside each heartbeat.  If
                        # enumeration fails, _build_reconcile_frame returns None and
                        # we skip — never send an empty list due to an error.
                        _hb_reconcile = _build_reconcile_frame()
                        if _hb_reconcile is not None:
                            try:
                                await ws.send(json.dumps(_hb_reconcile))
                            except Exception:
                                raise

                async def _watchdog() -> None:
                    while not stop.is_set():
                        await asyncio.sleep(HEARTBEAT_INTERVAL / 2)
                        if stop.is_set():
                            return
                        # last_pong is per-iteration shared state; closures are awaited within
                        # the same loop pass (see gather below), so the binding is not stale.
                        if time.monotonic() - last_pong[0] > HEARTBEAT_TIMEOUT:  # noqa: B023
                            log.warning(
                                "heartbeat timeout (%.0fs) — channel path presumed dead; reconnecting",
                                HEARTBEAT_TIMEOUT,
                            )
                            raise ConnectionError("heartbeat timeout")

                await asyncio.gather(_sender(), _receiver(), _heartbeat(), _watchdog())

                if stop.is_set():
                    return
            # Connection closed (clean drop or stream break) — reconnect at once
            # with a freshly-signed URL (no backoff for a healthy reconnect).
            continue
        except Exception:
            # Failed to establish, or an unexpected error — back off, then the
            # outer loop re-signs and retries.
            if stop.is_set():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
