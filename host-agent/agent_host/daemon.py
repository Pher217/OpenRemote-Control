"""
daemon.py — Main orchestration loop for the host-agent daemon.

run() starts the WebSocket sender (wsclient.run_sender), which connects to the
backend, receives drive commands (headless.prompt / pty.inject / session.start),
and streams their turns back. The old all-history transcript-observation poll
loop was removed (blocked heartbeats by scanning every transcript file). It is
replaced by scoped, single-session tailing via "tail.start"/"tail.stop" (see
agent_host.transcript_tail) — the daemon never scans a directory, it only
tails the one transcript file the backend explicitly asked for. On shutdown,
all active tails are stopped alongside the sender.

All heavy logic lives in the other modules; this file just wires them together.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from agent_host.config import HostConfig
from agent_host.queue import OfflineQueue
from agent_host.wsclient import _engines, _transcript_tails, run_sender

log = logging.getLogger(__name__)


def _queue_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "openremote-control" / "queue.jsonl"


def run(cfg: HostConfig) -> None:
    """Start the daemon (blocks until interrupted).

    Parameters
    ----------
    cfg:
        HostConfig loaded from disk.
    """
    queue = OfflineQueue(_queue_path())
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
        sender_task = asyncio.create_task(run_sender(cfg, queue, stop=stop))
        try:
            await sender_task
        except (KeyboardInterrupt, asyncio.CancelledError):
            stop.set()
            sender_task.cancel()
        finally:
            for tail in list(_transcript_tails.values()):
                await tail.stop()
            _transcript_tails.clear()
            for engine in list(_engines.values()):
                await asyncio.to_thread(engine.stop)
            _engines.clear()

    asyncio.run(_main())
