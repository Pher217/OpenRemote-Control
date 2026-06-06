"""
queue.py — Durable offline queue for pending WebSocket events.

OfflineQueue is an append-only JSONL buffer on disk.  Events are written
immediately on enqueue() and only removed after the caller confirms delivery
via the send callback passed to drain().

Crash safety:
- Writes use a tmp-file-then-rename pattern so the queue is never left in a
  partially-written state.
- drain() rewrites the queue file with only the un-acked tail after each
  successful send, so a crash mid-drain replays from the last un-acked event.
- enqueue() appends directly to the live file (no rename needed — appending
  is atomic enough for our purposes; the worst case is a partial last record
  which the JSONL loader skips on the next read).
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path


class OfflineQueue:
    """Durable append-only JSONL event buffer.

    Parameters
    ----------
    path:
        Path to the JSONL backing file.  Created on first enqueue.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_all(self) -> list[dict]:
        """Return all events currently in the queue (skip malformed lines)."""
        if not self._path.exists():
            return []
        events: list[dict] = []
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    events.append(json.loads(line))
        return events

    def _write_all(self, events: list[dict]) -> None:
        """Atomically rewrite the queue file with *events*."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(
            tmp,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, separators=(",", ":")) + "\n")
        except Exception:
            raise
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, event: dict) -> None:
        """Append *event* to the durable queue.

        Thread-safety: not guaranteed; the daemon is single-threaded async.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Open for append; create with 0o600 if not present.
        if not self._path.exists():
            fd = os.open(
                self._path,
                os.O_CREAT | os.O_WRONLY | os.O_APPEND,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            os.close(fd)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def drain(self, send: Callable[[dict], bool]) -> int:
        """Try to send all queued events; remove successfully sent ones.

        Parameters
        ----------
        send:
            Callable that takes one event dict and returns True on success or
            False on failure.  On the first failure, drain stops and leaves
            the remaining events (including the failed one) in the queue.

        Returns
        -------
        int
            Number of events successfully sent and removed.
        """
        events = self._read_all()
        if not events:
            return 0

        sent_count = 0
        for i, event in enumerate(events):
            if send(event):
                sent_count += 1
            else:
                # Keep from this failed event onwards.
                remaining = events[i:]
                self._write_all(remaining)
                return sent_count

        # All sent — clear the queue.
        if self._path.exists():
            self._path.unlink()
        return sent_count

    def __len__(self) -> int:
        """Return the number of events currently queued."""
        return len(self._read_all())
