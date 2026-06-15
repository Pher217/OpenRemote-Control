"""claude_headless.py — thin wrapper around `claude -p` for headless relay."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)

_CLAUDE_FALLBACK = "/Users/philippehermann/.local/bin/claude"
_EXTRA_PATH = "/Users/philippehermann/.local/bin:/opt/homebrew/bin"


def run_headless(
    prompt: str,
    claude_session_id: str,
    cwd: str,
    started: bool,
    timeout: int = 600,
) -> dict:
    """Run Claude headlessly; return {'text': str, 'is_error': bool}. Never raises.

    Parameters
    ----------
    prompt:
        The user prompt to send to Claude.
    claude_session_id:
        UUID string used to identify / resume the Claude session.
    cwd:
        Working directory for the subprocess.  Defaults to ~ when empty/None.
    started:
        True if the session was already started (uses --resume).
        False for the first turn (uses --session-id).
    timeout:
        Hard wall-clock timeout in seconds (default 600 = 10 min).
    """
    claude_bin = shutil.which("claude") or _CLAUDE_FALLBACK

    if started:
        session_args = ["--resume", claude_session_id]
    else:
        session_args = ["--session-id", claude_session_id]

    argv = [
        claude_bin,
        "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        *session_args,
    ]

    env = {
        **os.environ,
        "PATH": _EXTRA_PATH + ":" + os.environ.get("PATH", ""),
    }

    work_dir = cwd or os.path.expanduser("~")

    try:
        result = subprocess.run(
            argv,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        try:
            d = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("claude_headless: JSON parse error: %s — stdout=%r", exc, result.stdout[:200])
            return {"text": f"(headless run failed: JSON parse error: {exc})", "is_error": True}
        return {"text": d.get("result", ""), "is_error": bool(d.get("is_error"))}
    except subprocess.TimeoutExpired as exc:
        log.warning("claude_headless: timeout after %ds", timeout)
        return {"text": f"(headless run failed: timeout after {timeout}s)", "is_error": True}
    except Exception as exc:
        log.warning("claude_headless: unexpected error: %s", exc)
        return {"text": f"(headless run failed: {exc})", "is_error": True}
