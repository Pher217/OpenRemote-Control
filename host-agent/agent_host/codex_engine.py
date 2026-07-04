"""Per-turn Codex drive engine.

Drives a Codex session via ``codex exec`` / ``codex exec resume``, spawning ONE
subprocess per turn (codex exec has no persistent-stdin mode, unlike ``claude
-p``). The JSONL event stream from the first turn yields a ``thread.started``
event whose ``thread_id`` is captured as ``self._session_id`` and reused to
resume subsequent turns in the same session.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections import deque

log = logging.getLogger(__name__)
MAX_LINE_BYTES = 1_000_000


def _resolve_codex_bin() -> str:
    # Order: $ORC_CODEX_BIN -> $CODEX_CLI_PATH -> the app bundle path -> 'codex'.
    # The app binary is REQUIRED (the PATH 'codex' is an older version that
    # rejects this user's config); default to the app bundle path.
    explicit = os.environ.get("ORC_CODEX_BIN") or os.environ.get("CODEX_CLI_PATH")
    if explicit:
        return explicit
    app_bin = "/Applications/Codex.app/Contents/Resources/codex"
    if os.path.exists(app_bin):
        return app_bin
    return "codex"


class CodexEngine:
    def __init__(self, cwd: str, on_event, on_turn_complete, session_id: str | None = None) -> None:
        self.cwd = cwd
        self.on_event = on_event
        self.on_turn_complete = on_turn_complete
        # When set (bind), the FIRST turn does `codex exec resume <id>` to
        # continue the operator's discovered session (a forked snapshot);
        # otherwise the first turn starts a fresh session.
        self._session_id: str | None = session_id
        self._turn_in_flight = False
        self._pending: deque[str] = deque()
        self._lock = threading.Lock()
        self._stopped = False
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._finished_this_turn = False
        self._sandbox = os.environ.get("ORC_CODEX_SANDBOX", "workspace-write")

    def send(self, text: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._turn_in_flight:
                self._pending.append(text)
                return
            self._turn_in_flight = True
            self._finished_this_turn = False
            self._run_turn(text)

    def _run_turn(self, text: str) -> None:
        bin_ = _resolve_codex_bin()
        common = ["--json", "--skip-git-repo-check"]
        if self._session_id:
            # `codex exec resume` rejects --sandbox (verified live) — it inherits
            # the session's sandbox. Flags precede <session_id> <prompt>.
            argv = [bin_, "exec", "resume", *common, self._session_id, text]
        else:
            argv = [bin_, "exec", *common, "--sandbox", self._sandbox, text]

        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            # _run_turn always runs with self._lock already held (callers:
            # send / _finish / _read_loop EOF). Re-acquiring here would
            # deadlock — set the flag directly and notify.
            log.exception("failed to spawn codex exec")
            self._turn_in_flight = False
            self._safe(self.on_turn_complete, True)
            return

        self._proc = proc
        self._reader = threading.Thread(
            target=self._read_loop, args=(proc,), daemon=True
        )
        self._reader.start()
        watchdog = threading.Thread(
            target=self._watchdog, args=(proc,), daemon=True
        )
        watchdog.start()

    def _watchdog(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            log.warning("codex exec exceeded 600s; killing")
            try:
                proc.kill()
            except Exception:
                log.exception("watchdog kill failed")

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            if self._stopped:
                break
            if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = ev.get("type")
            if t == "thread.started":
                tid = ev.get("thread_id")
                if tid:
                    with self._lock:
                        self._session_id = tid
            elif t == "item.completed":
                item = ev.get("item") or {}
                if item.get("type") == "agent_message":
                    txt = (item.get("text") or "").strip()
                    if txt:
                        self._safe(self.on_event, txt)
            elif t == "turn.completed":
                self._finish(is_error=False)
            elif t in ("turn.failed", "error"):
                self._finish(is_error=True)

        with self._lock:
            if (
                not self._stopped
                and self._turn_in_flight
                and proc is self._proc
                and not self._finished_this_turn
            ):
                log.warning("codex engine died mid-turn")
                self._safe(self.on_turn_complete, True)
                if self._pending:
                    nexttext = self._pending.popleft()
                    self._finished_this_turn = False
                    self._run_turn(nexttext)
                else:
                    self._turn_in_flight = False

    def _finish(self, is_error: bool) -> None:
        with self._lock:
            if self._finished_this_turn or self._stopped:
                return
            self._finished_this_turn = True
            self._safe(self.on_turn_complete, is_error)
            if self._pending:
                nexttext = self._pending.popleft()
                self._finished_this_turn = False
                self._run_turn(nexttext)
            else:
                self._turn_in_flight = False

    def _safe(self, fn, *a) -> None:
        try:
            fn(*a)
        except Exception:
            log.exception("callback failed")

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            proc = self._proc
            if proc is None:
                return
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    log.exception("failed to kill codex process")
