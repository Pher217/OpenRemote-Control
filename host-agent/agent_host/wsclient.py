"""
wsclient.py — Authenticated WebSocket sender for the host daemon.

connect_url()
    Builds the signed WebSocket URL with a fresh timestamp and nonce.

run_sender()
    Async loop that connects (with automatic reconnect via websockets'
    async-for protocol), drains the offline queue, then forwards new events
    as they arrive.  The *connect* and *stop* parameters are injectable for
    testing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

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


async def run_sender(
    cfg: HostConfig,
    queue: OfflineQueue,
    *,
    connect: Any = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Connect to the backend WebSocket and stream queued + incoming events.

    Uses ``async for ws in connect(url)`` which provides automatic
    exponential-backoff reconnection built into websockets>=13.

    On each connection:
    1. Drain the offline queue (synchronously sends buffered events).
    2. Loop waiting for new events placed into an internal asyncio.Queue
       by the daemon's poll loop.

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
    """
    if connect is None:
        connect = websockets.connect

    if stop is None:
        stop = asyncio.Event()

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

                # Stream incoming events until disconnected or stopped.
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
                        break

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
