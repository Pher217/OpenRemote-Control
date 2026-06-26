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


def _safe_emit(on_event, text: str) -> None:
    try:
        on_event(text)
    except Exception:
        log.debug("claude_headless: on_event raised — ignoring", exc_info=True)


def run_headless_streaming(
    prompt: str,
    claude_session_id: str,
    cwd: str,
    started: bool,
    on_event,
    timeout: int = 600,
) -> dict:
    """Like run_headless, but streams progress: calls ``on_event(text)`` for each
    assistant text block and tool-use step as Claude works (via
    ``--output-format stream-json``), then returns the final {'text', 'is_error'}.

    The caller relays each on_event to the chat (coalesced into one edited message
    by the backend's ``progress`` delivery mode) so the operator sees Claude working
    live instead of one block at the end. Never raises. Same robust resume-or-create
    fallback as run_headless.
    """
    claude_bin = _resolve_claude_bin() or "claude"
    env = {**os.environ}
    work_dir = cwd or os.path.expanduser("~")
    resume = ["--resume", claude_session_id]
    create = ["--session-id", claude_session_id]
    order = [resume, create] if started else [create, resume]

    last = {"text": "(headless run failed: no attempt)", "is_error": True}
    for session_args in order:
        last, emitted = _stream_once(claude_bin, prompt, session_args, work_dir, env, timeout, on_event)
        if not last["is_error"]:
            return last
        # Never retry the alternate flag once the attempt has streamed any event:
        # re-running the same prompt under bypassPermissions would duplicate tool
        # side effects. Fallback is only for a clean init-time failure (e.g. wrong
        # --resume/--session-id), which errors before any event is emitted.
        if emitted:
            return last
    return last


def _stream_once(claude_bin, prompt, session_args, work_dir, env, timeout, on_event):
    """One ``claude -p --output-format stream-json`` run; emit per-event.

    Returns ``(result_dict, emitted_bool)`` where emitted is True if any assistant
    text / tool step was relayed. Never raises. A watchdog thread enforces *timeout*
    even when Claude is silent (the stdout iterator otherwise blocks forever and
    would hold the per-session lock); stderr is discarded so a full stderr pipe
    can't deadlock the child against the stdout reader.
    """
    import threading

    argv = [
        claude_bin,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        *session_args,
    ]
    final = {"text": "", "is_error": True}
    emitted = [False]
    saw_result = False
    proc = None

    def _emit(t: str) -> None:
        emitted[0] = True
        _safe_emit(on_event, t)

    try:
        proc = subprocess.Popen(
            argv, cwd=work_dir, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
    except FileNotFoundError:
        return ({
            "text": "(headless run failed: 'claude' not found on PATH; set ORC_CLAUDE_BIN)",
            "is_error": True,
        }, False)
    except Exception as exc:
        log.warning("claude_headless: stream spawn error: %s", exc)
        return ({"text": f"(headless run failed: {exc})", "is_error": True}, False)

    done = threading.Event()
    killed = threading.Event()

    def _watchdog() -> None:
        if not done.wait(timeout):
            killed.set()
            try:
                proc.kill()
            except Exception:
                pass

    watchdog = threading.Thread(target=_watchdog, daemon=True)
    watchdog.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue  # non-JSON noise line — skip
            etype = ev.get("type")
            if etype == "assistant":
                for block in (ev.get("message", {}).get("content") or []):
                    bt = block.get("type")
                    if bt == "text":
                        txt = (block.get("text") or "").strip()
                        if txt:
                            final["text"] = txt
                            _emit(txt)
                    elif bt == "tool_use":
                        _emit(f"🔧 {block.get('name', 'tool')}")
            elif etype == "result":
                saw_result = True
                final["is_error"] = bool(ev.get("is_error"))
                if ev.get("result"):
                    final["text"] = ev["result"]
    except Exception as exc:
        log.warning("claude_headless: stream read error: %s", exc)
        final = {"text": f"(headless run failed: {exc})", "is_error": True}
    finally:
        done.set()
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if killed.is_set():
        return ({"text": f"(headless run failed: timeout after {timeout}s)", "is_error": True}, emitted[0])
    if not saw_result and not final["text"]:
        return ({"text": "(headless run failed: no result from stream)", "is_error": True}, emitted[0])
    return (final, emitted[0])
