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

from agent_host.input_policy import classify_input


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

    def kill(self, name: str) -> None:
        """Kill the tmux session named *name*.

        No-op if the session does not exist.
        """
        server = self._server()
        session = server.sessions.get(session_name=name, default=None)
        if session is not None:
            session.kill()

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
        # suppress_history=False keeps the command in the shell's history
        # which aids auditability.
        pane.send_keys(text, enter=False, suppress_history=False)
