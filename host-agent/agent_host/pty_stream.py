"""pty_stream.py — Shared capture/stream loop for PTY sessions.

Extracted from run_cmd.py so that both ``orc-host run`` and the
``session.start`` WebSocket handler can share the same streaming logic.

``stream_pty_output(ws, pty, session_name)``
    Polls the named tmux session every second, diffs output line-by-line,
    and sends new lines as ``session.pty_output`` frames.  Sends a final
    ``session.pty_end`` frame when the session exits.

Callers are responsible for:
  - creating the PTY session (via PtySession.start) before calling this,
  - sending the ``session.pty_start`` frame (this function does NOT send it),
  - the WebSocket being open for the duration.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences from *text*."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


async def stream_pty_output(ws, pty, session_name: str) -> None:
    """Stream output from a running PTY session over *ws*.

    Polls every second while the session lives.  After the session exits,
    does one final capture to catch output produced just before tmux
    destroyed the session, then sends a ``session.pty_end`` frame.

    Parameters
    ----------
    ws:
        Open WebSocket connection.  Must support ``await ws.send(str)``.
    pty:
        A ``PtySession`` instance.  Must support ``exists(name)`` and
        ``capture(name)``.
    session_name:
        Name of the tmux session to stream.
    """
    import asyncio  # noqa: PLC0415 — stdlib, always available

    sent_lines: int = 0

    def _try_capture() -> str | None:
        """Capture current PTY output; return None if session gone."""
        try:
            return pty.capture(session_name)
        except KeyError:
            return None

    async def _send_diff(raw: str) -> None:
        """Line-based diff: ship only newly appeared lines.

        capture-pane -S -2000 returns scrollback + visible screen, so the
        captured text grows append-only as long as total output stays within
        the history window.  We track how many lines have already been
        shipped (sent_lines) and send only the tail.

        Re-baseline branch: if the capture is unexpectedly shorter than
        sent_lines (e.g. tmux history flushed, pane resized, screen cleared),
        we accept a small one-time loss rather than re-shipping all content
        and flooding the peer with duplicates.
        """
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
                    await ws.send(json.dumps({
                        "type": "session.pty_output",
                        "data": {
                            "session_name": session_name,
                            "text": new_text,
                        },
                    }))
                except Exception as exc:
                    log.warning("stream_pty_output: ws send failed, continuing: %s", exc)
        sent_lines = len(lines)

    # --- capture/diff loop ---
    while pty.exists(session_name):
        raw = _try_capture()
        if raw is not None:
            await _send_diff(raw)
        await asyncio.sleep(1.0)

    # Final capture for output produced right before session exit.
    raw = _try_capture()
    if raw is not None:
        await _send_diff(raw)

    # --- pty_end frame ---
    try:
        await ws.send(json.dumps({
            "type": "session.pty_end",
            "data": {"session_name": session_name},
        }))
    except Exception as exc:
        log.warning("stream_pty_output: failed to send pty_end: %s", exc)
