"""run_cmd.py — implementation of `orc-host run <command...>`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import uuid

import websockets

from agent_host.config import load
from agent_host.pty_session import PtySession
from agent_host.wsclient import connect_url

log = logging.getLogger(__name__)


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences from *text*."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


async def run_pty(cfg, command, session_name=None, cwd=None):
    """Launch *command* in a detached tmux PTY session and stream output over WebSocket.

    Parameters
    ----------
    cfg:
        HostConfig with backend_url, host_id, and token.
    command:
        Shell command string to run inside the PTY session.
    session_name:
        Tmux session name.  Auto-generated if None.
    cwd:
        Working directory for the PTY session.  Defaults to tmux server default.
    """
    if session_name is None:
        session_name = f"orc-{uuid.uuid4().hex[:8]}"

    pty = PtySession()
    pty.start(session_name, command, cwd)

    url = connect_url(cfg.backend_url, cfg)

    try:
        async with websockets.connect(url) as ws:
            # --- pty_start frame ---
            await ws.send(json.dumps({
                "type": "session.pty_start",
                "data": {
                    "session_name": session_name,
                    "command": command,
                    "cwd": cwd or "",
                },
            }))

            # Number of lines already shipped to the peer.
            sent_lines: int = 0

            def _try_capture() -> str | None:
                """Capture current PTY output (with scrollback), return None if session gone."""
                try:
                    return pty.capture(session_name)
                except KeyError:
                    return None

            async def _send_diff(raw: str) -> None:
                """Line-based diff: ship only newly appeared lines.

                capture-pane -S -2000 returns the scrollback + visible screen,
                so the captured text grows append-only as long as total output
                stays within the history window.  We track how many lines have
                already been shipped (sent_lines) and send only the tail.

                Re-baseline branch: if the capture is unexpectedly shorter than
                sent_lines (e.g. tmux history flushed, pane resized, screen
                cleared), we accept a small one-time loss rather than re-shipping
                all content and flooding the peer with duplicates.  We simply
                update sent_lines to the new length and skip this tick.
                """
                nonlocal sent_lines
                # Strip trailing blank rows (empty pane padding) so they don't
                # inflate sent_lines or get shipped as content.
                content = strip_ansi(raw).rstrip()
                lines = content.split("\n") if content else []

                if len(lines) < sent_lines:
                    # Capture shrank — re-baseline conservatively, skip this tick.
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
                            log.warning("run_pty: ws send failed, continuing: %s", exc)
                sent_lines = len(lines)

            # --- capture/diff loop ---
            # Poll every second while the session lives.
            # After the session exits, do one final capture to catch output
            # produced just before tmux destroyed the session.
            while pty.exists(session_name):
                raw = _try_capture()
                if raw is not None:
                    await _send_diff(raw)
                await asyncio.sleep(1.0)

            # --- pty_end frame ---
            try:
                await ws.send(json.dumps({
                    "type": "session.pty_end",
                    "data": {"session_name": session_name},
                }))
            except Exception as exc:
                log.warning("run_pty: failed to send pty_end: %s", exc)

    except KeyboardInterrupt:
        # Leave tmux session running for inspection.
        print(f"\nPTY session left running: {session_name}")
        sys.exit(0)


def cmd_run(args):
    """Synchronous entry point called by cli.py."""
    cfg = load()
    if cfg is None:
        print(
            "Error: no config found. Run 'orc-host enroll' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    command = " ".join(args.command)
    asyncio.run(run_pty(cfg, command, session_name=args.name, cwd=args.cwd))
