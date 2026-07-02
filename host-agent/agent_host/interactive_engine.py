"""Persistent Claude Code stream-json subprocess manager.

Owns ONE long-lived ``claude -p --input-format stream-json --output-format
stream-json`` process per session, so individual turns need no per-turn
respawn. A daemon reader thread consumes the subprocess stdout line stream and
forwards events to the supplied callbacks.
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


class InteractiveEngine:
    def __init__(
        self,
        claude_session_id: str,
        cwd: str,
        on_event,
        on_turn_complete,
    ) -> None:
        self.claude_session_id = claude_session_id
        self.cwd = cwd
        self.on_event = on_event
        self.on_turn_complete = on_turn_complete
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._turn_in_flight = False
        self._pending: deque[str] = deque()
        self._lock = threading.Lock()
        self._stopped = False
        self._respawned_this_send = False
        self._started_once = False

    def _resolve_bin(self) -> str:
        return os.environ.get("ORC_CLAUDE_BIN") or "claude"

    def _spawn(self, resume: bool) -> None:
        sid = self.claude_session_id
        argv = [
            self._resolve_bin(),
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
        ]
        if resume:
            argv.extend(["--resume", sid])
        else:
            argv.extend(["--session-id", sid])

        proc = subprocess.Popen(
            argv,
            cwd=self.cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._proc = proc
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(proc,),
            daemon=True,
        )
        self._reader.start()

    def send(self, text: str) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._turn_in_flight:
                self._pending.append(text)
                return
            self._turn_in_flight = True
            self._respawned_this_send = False
            self._write_user_line(text)

    def _write_user_line(self, text: str) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._spawn(resume=self._started_once)
            proc = self._proc

        payload = {
            "type": "user",
            "message": {"role": "user", "content": text},
        }
        line = json.dumps(payload) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            if not self._respawned_this_send:
                self._respawned_this_send = True
                self._spawn(resume=True)
                proc = self._proc
                try:
                    proc.stdin.write(line)
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    log.exception("failed to write after respawn")
                    self._turn_in_flight = False
                    self._safe(self.on_turn_complete, True)
                    return
            else:
                log.exception("failed to write user line")
                self._turn_in_flight = False
                self._safe(self.on_turn_complete, True)
                return
        self._started_once = True

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            if self._stopped:
                # A recycled/stopped engine must not emit late events into
                # callbacks whose turn has already been failed by the caller.
                break
            if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ev_type = ev.get("type")
            if ev_type == "assistant":
                msg = ev.get("message", {})
                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        txt = block.get("text", "")
                        if txt and txt.strip():
                            self._safe(self.on_event, txt)
                    elif btype == "tool_use":
                        name = block.get("name") or "tool"
                        self._safe(self.on_event, f"🔧 {name}")

            elif ev_type == "result":
                is_err = bool(ev.get("is_error")) or ev.get("subtype") != "success"
                self._safe(self.on_turn_complete, is_err)
                with self._lock:
                    if self._pending:
                        self._write_user_line(self._pending.popleft())
                    else:
                        self._turn_in_flight = False

        with self._lock:
            # Only the CURRENT process's reader may fail the turn — a stale
            # reader (its process was replaced by a mid-send respawn) hitting
            # EOF must not complete a turn the new process is handling.
            if not self._stopped and self._turn_in_flight and proc is self._proc:
                log.warning("engine died mid-turn")
                self._safe(self.on_turn_complete, True)
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
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                log.exception("failed to close stdin")
            try:
                proc.wait(timeout=10)
            except Exception:
                log.warning("claude process did not exit in time; killing")
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    log.exception("failed to kill process")
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5)
