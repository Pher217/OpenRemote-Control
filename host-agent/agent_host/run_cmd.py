"""run_cmd.py — implementation of `orc-host run <command...>`."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

import websockets

from agent_host.config import load
from agent_host.pty_session import PtySession
from agent_host.pty_stream import stream_pty_output
from agent_host.wsclient import connect_url

log = logging.getLogger(__name__)


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
                        handle_host_command(frame)

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
    asyncio.run(run_pty(cfg, command, session_name=args.name, cwd=args.cwd))
