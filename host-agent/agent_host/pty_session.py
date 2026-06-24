"""
pty_session.py — Thin tmux-backed PTY session manager for 'orc run'.

Legitimacy note
---------------
Every PTY session managed here is **created by this process** via
``tmux new-session``.  Because we launched the child process, we own its
stdin; injecting keystrokes via ``tmux send-keys`` is equivalent to the
operator typing at the keyboard.  We are NOT attaching to or hijacking a
pre-existing session that belongs to another user or process.

Approval gate
-------------
``send_keys`` enforces an approval gate:

* ``approved=True``  — caller asserts the input has been reviewed and
  authorised by a human operator.  Injection proceeds (after the policy
  classifier's own checks for control sequences and dangerous patterns).
* ``approved=False`` — if the policy classifier marks the input as
  ``requires_approval=True``, a ``PermissionError`` is raised *before* any
  libtmux call is made.  This means the gate can be unit-tested without a
  running tmux server.

libtmux import is lazy
----------------------
``import libtmux`` happens inside methods at call time, not at module import.
Importing ``agent_host.pty_session`` (or ``agent_host`` itself) therefore
never fails in environments where libtmux is not installed (e.g. CI runners
that only test the pure-Python safety core).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from agent_host.input_policy import classify_input

log = logging.getLogger(__name__)

# Process-local registry of tmux sessions THIS process started. tmux itself does
# not record which OS process created a session, so ownership is tracked here.
# pty.inject is broadcast to every host ws connection in the host group (the
# observe daemon plus each `orc run`); without an ownership check, every
# connection runs `tmux send-keys` and the keystrokes are injected N times. The
# owning process is the one that called start() for the session.
#
# Persistence (daemon only): the long-lived daemon enables a file-backed registry
# via configure_persistence() so its session.start sessions stay injectable across
# daemon restarts (the tmux sessions outlive the process). `orc run` does NOT
# persist — its registry is ephemeral and dies with the (session-scoped) process.
_started_sessions: set[str] = set()
_persist_path: Path | None = None


def configure_persistence(path) -> None:
    """Enable file-backed ownership and load any previously-persisted set.

    Call once at daemon startup. Idempotent-ish: re-loads from disk each call.
    Corrupt/unreadable state fails open to an empty set (a stale name only risks
    one harmless inject to a dead session, caught as KeyError).
    """
    global _persist_path
    _persist_path = Path(path)
    try:
        if _persist_path.exists():
            loaded = json.loads(_persist_path.read_text())
            if isinstance(loaded, list):
                _started_sessions.update(str(n) for n in loaded)
    except Exception:
        pass


def _flush() -> None:
    if _persist_path is None:
        return
    try:
        _persist_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a crash mid-write can't corrupt the ownership file.
        tmp = _persist_path.with_suffix(_persist_path.suffix + ".tmp")
        tmp.write_text(json.dumps(sorted(_started_sessions)))
        tmp.replace(_persist_path)
    except Exception as exc:
        # A failed flush silently degrades restart-survival of inject — surface it.
        log.warning("pty ownership flush to %s failed: %s", _persist_path, exc)


def mark_started(name: str) -> None:
    """Record that this process started the tmux session *name*."""
    _started_sessions.add(name)
    _flush()


def mark_stopped(name: str) -> None:
    """Forget a session this process started (on kill / end)."""
    _started_sessions.discard(name)
    _flush()


def prune_to_live(live_names) -> None:
    """Drop owned sessions that are no longer live tmux sessions.

    Called from the daemon's reconcile cycle so a session that exited naturally
    (not via kill()) is released — preventing an unbounded stale set and the
    name-reuse duplicate-inject footgun Codex flagged.
    """
    live = set(live_names)
    stale = _started_sessions - live
    if stale:
        _started_sessions.difference_update(stale)
        _flush()


def was_started_here(name: str) -> bool:
    """True if *name* was started by this process (eligible for inject)."""
    return name in _started_sessions

# Submit timing for a full-screen TUI (e.g. claude). The TUI ingests pasted text
# asynchronously (char-by-char render / bracketed paste); an Enter that arrives
# before the text has landed hits an empty input, so the prompt sits unsubmitted
# in the box (observed live with claude — text visible, never sent). We therefore
# wait for the text to SETTLE before the first Enter, scaling the wait with text
# length, then send a second spaced Enter as a dropped-submit backstop (a no-op
# on already-submitted/empty input, so it never double-submits real text).
_SUBMIT_SETTLE_BASE = 0.8
_SUBMIT_SETTLE_PER_CHAR = 0.003
_SUBMIT_SETTLE_MAX = 3.0
_SUBMIT_KEY_DELAY = 0.15


def _submit_settle_seconds(text: str) -> float:
    """Seconds to wait for a TUI to ingest `text` before submitting."""
    return min(_SUBMIT_SETTLE_MAX, _SUBMIT_SETTLE_BASE + len(text) * _SUBMIT_SETTLE_PER_CHAR)


class PtySession:
    """Manage named, detached tmux sessions for AI-CLI subprocesses.

    Each session is identified by a *name* string (must be a valid tmux
    session name: no dots, colons, or spaces recommended).

    All tmux interactions are performed through ``libtmux``, which is
    imported lazily so that this module is importable without libtmux
    installed.
    """

    # ------------------------------------------------------------------
    # Lazy libtmux accessor
    # ------------------------------------------------------------------

    @staticmethod
    def _server():  # type: ignore[return]
        """Return a connected ``libtmux.Server`` instance.

        Raises ``ImportError`` if libtmux is not installed.
        Raises ``libtmux.exc.LibTmuxException`` if tmux is not running or
        not on PATH.
        """
        import libtmux  # noqa: PLC0415 — intentional lazy import
        return libtmux.Server()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        name: str,
        command: str,
        cwd: str | None = None,
    ) -> None:
        """Create a new detached tmux session running *command*.

        Parameters
        ----------
        name:
            Unique session identifier.  Must not already exist.
        command:
            Shell command to run as the session's initial window command,
            e.g. ``"claude"`` or ``"codex --model o4-mini"``.
        cwd:
            Working directory for the session.  Defaults to the tmux
            server's default (typically the caller's cwd).

        Raises
        ------
        ValueError
            If a session named *name* already exists.
        """
        server = self._server()
        if self.exists(name):
            raise ValueError(f"PTY session {name!r} already exists")

        kwargs: dict = {
            "session_name": name,
            "attach": False,
            "window_command": command,
        }
        if cwd is not None:
            kwargs["start_directory"] = cwd

        server.new_session(**kwargs)
        mark_started(name)

    def kill(self, name: str) -> None:
        """Kill the tmux session named *name*.

        No-op if the session does not exist.
        """
        server = self._server()
        session = server.sessions.get(session_name=name, default=None)
        if session is not None:
            session.kill()
        mark_stopped(name)

    def list_live_sessions(self) -> list[str]:
        """Names of all live tmux sessions on this host.

        Returns an empty list when tmux has zero sessions.  Any exception from
        libtmux (e.g. no tmux server running, enumeration failure) propagates
        to the caller — the caller MUST treat that as "unknown" and NOT send a
        reconcile frame, to avoid falsely marking every session dead.
        """
        server = self._server()
        return [s.name for s in server.sessions]

    def exists(self, name: str) -> bool:
        """Return ``True`` if a session named *name* currently exists."""
        server = self._server()
        return server.sessions.get(session_name=name, default=None) is not None

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def capture(self, name: str, history: int = 2000) -> str:
        """Return a plain-text snapshot of the session pane including scrollback.

        Uses ``capture-pane -p -S -<history>`` to include up to *history* lines
        of scrollback above the visible screen.  This makes the captured text
        grow append-only as output scrolls past the visible pane height, which
        is required for a correct line-based diff in the streaming loop.

        Parameters
        ----------
        name:
            Session name to capture.
        history:
            Number of scrollback lines to include above the visible screen.
            Defaults to 2000.  Set to 0 to capture the visible screen only
            (reproduces the old behaviour, not recommended for streaming).

        Raises
        ------
        KeyError
            If the session does not exist.
        """
        server = self._server()
        session = server.sessions.get(session_name=name, default=None)
        if session is None:
            raise KeyError(f"PTY session {name!r} not found")

        pane = session.active_window.active_pane
        return "\n".join(pane.cmd("capture-pane", "-p", "-S", f"-{history}").stdout)

    def send_keys(
        self,
        name: str,
        text: str,
        *,
        approved: bool,
    ) -> None:
        """Inject *text* into the tmux session named *name*.

        The approval gate is enforced here **before** any libtmux call so
        that it can be tested without a running tmux server:

        1. ``input_policy.classify_input`` is called on *text*.
        2. If ``requires_approval`` is ``True`` **and** ``approved`` is
           ``False``, ``PermissionError`` is raised immediately.
        3. If the policy marks input as ``DANGEROUS`` (even with
           ``approved=True``), ``PermissionError`` is raised — dangerous
           inputs must be blocked even if a caller mistakenly passes
           ``approved=True``.  Only ``SAFE`` and ``REVIEW`` inputs reach
           the tmux layer (REVIEW only when ``approved=True``).
        4. For control sequences the policy will already classify as
           DANGEROUS, so step 3 catches them before libtmux is touched.

        Parameters
        ----------
        name:
            Target session name.
        text:
            Exact string to inject.  Normally a single line ending with
            ``\\n``.
        approved:
            ``True`` if a human operator has explicitly authorised this
            injection.

        Raises
        ------
        PermissionError
            If ``requires_approval`` is True and ``approved`` is False, or
            if the policy classifies input as DANGEROUS.
        KeyError
            If the session does not exist.
        """
        from agent_host.input_policy import Risk  # noqa: PLC0415

        result = classify_input(text)

        # Always block DANGEROUS inputs — even if the caller claims approved.
        if result["risk"] == Risk.DANGEROUS:
            reasons = "; ".join(result["reasons"])
            raise PermissionError(
                f"Input classified as DANGEROUS and cannot be injected: {reasons}"
            )

        # Block REVIEW inputs when no human approval was given.
        if result["requires_approval"] and not approved:
            reasons = "; ".join(result["reasons"])
            raise PermissionError(
                f"Input requires operator approval before injection: {reasons}"
            )

        # --- Gate passed; proceed with tmux injection ---
        server = self._server()
        session = server.sessions.get(session_name=name, default=None)
        if session is None:
            raise KeyError(f"PTY session {name!r} not found")

        pane = session.active_window.active_pane
        stripped = text.rstrip("\n")
        # Type the prompt WITHOUT submitting (suppress_history=False keeps it in
        # shell history for auditability).
        pane.send_keys(stripped, enter=False, suppress_history=False)
        # Wait for the TUI to fully ingest the pasted text BEFORE the first Enter.
        # Sending Enter too soon (the old 0.12s) lands it on an empty input and
        # the prompt sits unsubmitted (observed live with claude). The settle wait
        # scales with text length; a second spaced Enter backstops a dropped first
        # one and is a harmless no-op on already-submitted/empty input.
        time.sleep(_submit_settle_seconds(stripped))
        pane.cmd("send-keys", "Enter")
        time.sleep(_SUBMIT_KEY_DELAY)
        pane.cmd("send-keys", "Enter")
