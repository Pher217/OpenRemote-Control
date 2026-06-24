"""
daemon.py — Main orchestration loop for the host-agent daemon.

run() starts two concurrent tasks:
  1. A poll loop that discovers JSONL files, reads new lines, and enqueues events.
  2. The WebSocket sender (wsclient.run_sender) that drains the queue and streams
     events to the backend.

All heavy logic lives in the other modules; this file just wires them together.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from agent_host.config import HostConfig
from agent_host.discovery import iter_files
from agent_host.queue import OfflineQueue
from agent_host.tailer import OffsetStore, read_new_lines
from agent_host.wsclient import MAX_EVENT_BYTES, run_sender

log = logging.getLogger(__name__)

_DEFAULT_RUNTIMES = ["claude_code"]


def _queue_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "openremote-control" / "queue.jsonl"


async def _poll_loop(
    cfg: HostConfig,
    queue: OfflineQueue,
    runtimes: Sequence[str],
    offsets: OffsetStore,
    poll_interval: float,
    stop: asyncio.Event,
) -> None:
    """Discover files, read new lines, enqueue events."""
    while not stop.is_set():
        for provider in runtimes:
            for path in iter_files(provider):
                offset = offsets.get(path)
                lines, new_offset = read_new_lines(path, offset)
                if new_offset != offset:
                    offsets.set(path, new_offset)
                for line in lines:
                    raw = line.rstrip("\n")
                    if not raw:
                        continue
                    event: dict = {
                        "type": "session.line",
                        "data": {
                            "provider": provider,
                            "jsonl_path": path,
                            "raw": raw,
                        },
                    }
                    # Guard against oversized events (e.g. subagent 'attachment'
                    # lines that embed base64-encoded files).  Truncate the raw
                    # field so the event stays valid JSON under the limit rather
                    # than skipping it entirely — partial context is better than
                    # no context for diagnostic purposes.
                    encoded = json.dumps(event).encode("utf-8")
                    if len(encoded) > MAX_EVENT_BYTES:
                        overhead = len(encoded) - len(raw.encode("utf-8"))
                        # Allow some slack for the JSON encoding of the truncated string.
                        max_raw_bytes = MAX_EVENT_BYTES - overhead - 64
                        if max_raw_bytes <= 0:
                            log.warning(
                                "Skipping oversized event from %s (%d bytes)",
                                path,
                                len(encoded),
                            )
                            continue
                        truncated_raw = raw.encode("utf-8")[:max_raw_bytes].decode(
                            "utf-8", errors="ignore"
                        )
                        log.warning(
                            "Truncating oversized event from %s (%d bytes -> ~%d bytes)",
                            path,
                            len(encoded),
                            max_raw_bytes + overhead,
                        )
                        event["data"]["raw"] = truncated_raw + " [truncated]"
                        event = json.loads(json.dumps(event))  # Verify valid JSON.
                    queue.enqueue(event)
                    # Also push directly to the sender's incoming queue if connected.
                    incoming: asyncio.Queue | None = getattr(cfg, "_incoming_queue", None)
                    if incoming is not None:
                        await incoming.put(event)

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(
                asyncio.shield(asyncio.get_event_loop().create_future()),
                timeout=poll_interval,
            )


def run(
    cfg: HostConfig,
    *,
    runtimes: Sequence[str] | None = None,
    poll_interval: float = 2.0,
) -> None:
    """Start the daemon (blocks until interrupted).

    Parameters
    ----------
    cfg:
        HostConfig loaded from disk.
    runtimes:
        Providers to observe.  Defaults to ['claude_code'].
    poll_interval:
        Seconds between discovery/tail polls.
    """
    _runtimes = list(runtimes) if runtimes is not None else _DEFAULT_RUNTIMES
    queue = OfflineQueue(_queue_path())
    offsets = OffsetStore()
    stop = asyncio.Event()

    # Persist PTY-session ownership so session.start sessions launched by this
    # daemon stay injectable across daemon restarts (the tmux sessions outlive
    # the process). Reconcile prunes dead names. See pty_session.configure_persistence.
    from agent_host.pty_session import (  # noqa: PLC0415
        PtySession,
        configure_persistence,
        prune_to_live,
    )

    configure_persistence(_queue_path().parent / "owned-sessions.json")
    # Immediately drop persisted names whose tmux session died while the daemon
    # was down, so a reused name can't be double-injected before the first
    # reconcile cycle. Best-effort: if tmux can't be enumerated, reconcile prunes.
    try:
        prune_to_live(PtySession().list_live_sessions())
    except Exception:
        pass

    async def _main() -> None:
        sender_task = asyncio.create_task(
            run_sender(cfg, queue, stop=stop)
        )
        poll_task = asyncio.create_task(
            _poll_loop(cfg, queue, _runtimes, offsets, poll_interval, stop)
        )
        try:
            await asyncio.gather(sender_task, poll_task)
        except (KeyboardInterrupt, asyncio.CancelledError):
            stop.set()
            sender_task.cancel()
            poll_task.cancel()

    asyncio.run(_main())
