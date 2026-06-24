"""run_cmd.py — implementation of `orc-host run <command...>`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import sys
import uuid

import websockets

from agent_host.config import load
from agent_host.pty_session import PtySession
from agent_host.pty_stream import stream_pty_output
from agent_host.wsclient import connect_url

log = logging.getLogger(__name__)


def ensure_claude_session_id(command: str) -> tuple[str, str | None]:
    """Tie a `claude` PTY launch to a fixed JSONL transcript via ``--session-id``.

    If *command* invokes ``claude`` without an explicit ``--session-id``, append a
    fresh UUID so the PTY session and Claude's JSONL transcript share one id — the
    backend keys a single canonical thread on it, so clean output (parsed from the
    transcript) and input (tmux send-keys) land in one Telegram topic.

    Returns ``(command, claude_session_id)``. Non-claude commands are returned
    unchanged with ``None``. An explicit ``--session-id`` is honoured as-is.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return command, None
    if not parts:
        return command, None
    if os.path.basename(parts[0]) != "claude":
        return command, None
    # Honour an explicit id in either form: `--session-id X` or `--session-id=X`.
    for idx, part in enumerate(parts):
        if part == "--session-id":
            return command, (parts[idx + 1] if idx + 1 < len(parts) else None)
        if part.startswith("--session-id="):
            return command, (part.split("=", 1)[1] or None)
    sid = str(uuid.uuid4())
    return shlex.join([*parts, "--session-id", sid]), sid


async def run_pty(cfg, command, session_name=None, cwd=None, claude_session_id=None):
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
    claude_session_id:
        Claude's JSONL session UUID when the command is a `claude` launch — sent
        in the pty_start frame so the backend can unify this PTY thread with the
        transcript-observation thread.
    """
    if session_name is None:
        session_name = f"orc-{uuid.uuid4().hex[:8]}"

    pty = PtySession()
    pty.start(session_name, command, cwd)

    url = connect_url(cfg.backend_url, cfg)

    try:
        async with websockets.connect(url) as ws:
            # --- pty_start frame ---
            pty_start_data = {
                "session_name": session_name,
                "command": command,
                "cwd": cwd or "",
            }
            if claude_session_id:
                pty_start_data["claude_session_id"] = claude_session_id
            await ws.send(json.dumps({
                "type": "session.pty_start",
                "data": pty_start_data,
            }))

            # Run output streaming and inbound command handling concurrently so
            # pty.inject frames delivered to this ws connection are processed
            # (previously stream_pty_output ran alone with no recv loop, causing
            # inject frames sent via group_send to be silently dropped here).
            stream_done = asyncio.Event()

            async def _stream() -> None:
                await stream_pty_output(ws, pty, session_name)
                stream_done.set()

            async def _recv() -> None:
                from agent_host.wsclient import handle_host_command  # noqa: PLC0415

                while not stream_done.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except TimeoutError:
                        continue
                    except Exception:
                        break
                    try:
                        frame = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(frame, dict) and frame.get("type") == "host_command":
                        # This process owns exactly its own PTY session — only
                        # inject that one, so a broadcast inject for another
                        # session (handled by its owner) is not duplicated here.
                        handle_host_command(
                            frame, owns_session=lambda n: n == session_name
                        )

            await asyncio.gather(_stream(), _recv())

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
    command, claude_session_id = ensure_claude_session_id(command)
    asyncio.run(run_pty(
        cfg, command, session_name=args.name, cwd=args.cwd,
        claude_session_id=claude_session_id,
    ))
