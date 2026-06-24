"""claude_headless.py — thin wrapper around `claude -p` for headless relay."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)


def _resolve_claude_bin() -> str | None:
    """Locate the claude binary cross-platform.

    Order: explicit ``$ORC_CLAUDE_BIN`` override → PATH lookup (finds
    ``claude`` / ``claude.cmd`` / ``claude.exe`` on Windows). The daemon's PATH
    is augmented by run-daemon.sh (``$ORC_PATH_EXTRA``) so ``~/.local/bin`` is
    reachable under launchd. Returns None when not found so the caller fails
    with a clear message instead of exec'ing a hardcoded, machine-specific path.
    """
    override = os.environ.get("ORC_CLAUDE_BIN")
    if override:
        return override
    return shutil.which("claude")


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
    # Bare "claude" fallback keeps this cross-platform (resolved on PATH at exec
    # time, incl. claude.cmd/.exe on Windows) with no hardcoded machine path.
    claude_bin = _resolve_claude_bin() or "claude"
    env = {**os.environ}
    work_dir = cwd or os.path.expanduser("~")

    resume = ["--resume", claude_session_id]
    create = ["--session-id", claude_session_id]
    # Try the hinted flag first, then fall back to the other. `--resume` works
    # only for an existing session; `--session-id` only for a NEW one (reusing it
    # on an existing session errors). The `started` flag is a hint, not truth
    # (a daemon restart or a dropped first reply can desync it), so fall back —
    # this is a robust "resume or create".
    order = [resume, create] if started else [create, resume]

    last = {"text": "(headless run failed: no attempt)", "is_error": True}
    for session_args in order:
        last = _run_once(claude_bin, prompt, session_args, work_dir, env, timeout)
        if not last["is_error"]:
            return last
    return last


def _run_once(claude_bin, prompt, session_args, work_dir, env, timeout) -> dict:
    """Run a single `claude -p` invocation; parse the JSON result. Never raises."""
    argv = [
        claude_bin,
        "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        *session_args,
    ]
    try:
        result = subprocess.run(
            argv, cwd=work_dir, capture_output=True, text=True, timeout=timeout, env=env,
        )
        try:
            d = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning(
                "claude_headless: JSON parse error (%s): %s — stderr=%r",
                session_args[0], exc, (result.stderr or "")[:200],
            )
            return {"text": f"(headless run failed: {exc})", "is_error": True}
        return {"text": d.get("result", ""), "is_error": bool(d.get("is_error"))}
    except subprocess.TimeoutExpired:
        log.warning("claude_headless: timeout after %ds", timeout)
        return {"text": f"(headless run failed: timeout after {timeout}s)", "is_error": True}
    except FileNotFoundError:
        log.warning("claude_headless: claude binary not found (%r)", claude_bin)
        return {
            "text": "(headless run failed: 'claude' not found on PATH; set ORC_CLAUDE_BIN)",
            "is_error": True,
        }
    except Exception as exc:
        log.warning("claude_headless: unexpected error: %s", exc)
        return {"text": f"(headless run failed: {exc})", "is_error": True}
