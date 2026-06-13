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
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

import websockets

from agent_host.config import HostConfig
from agent_host.queue import OfflineQueue
from agent_host.signing import sign

log = logging.getLogger(__name__)

# Events whose JSON encoding exceeds this byte threshold are dropped rather than
# sent — a single oversized frame will cause the server to close the connection
# and would poison the offline queue indefinitely if retried.
MAX_EVENT_BYTES = 1_000_000


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


def handle_host_command(frame: dict, incoming_queue: asyncio.Queue | None = None) -> None:
    """Default handler for inbound host_command frames from the backend.

    Dispatches on frame["command"]:
    - "ping": logs receipt and, if an outbound queue is available, enqueues
      a host_command_ack event so the round-trip is observable.
    - "pty.inject": reserved for Phase 4 (PTY keystroke injection).
    - anything else: logged as unknown and ignored.

    Parameters
    ----------
    frame:
        Parsed JSON frame received from the backend.  Must contain at least
        ``{"type": "host_command", "command": "<name>"}``.
    incoming_queue:
        The internal asyncio.Queue used by run_sender to ship outbound events.
        When provided, a "ping" will enqueue an ack back to the backend.
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
    else:
        log.warning("host_command: unknown command %r — ignoring", command)


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
        on_command = handle_host_command

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
                            try:
                                on_command(frame)
                            except Exception:
                                log.exception("on_command raised — ignoring")
                        # All other types are silently ignored.

                await asyncio.gather(_sender(), _receiver())

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
