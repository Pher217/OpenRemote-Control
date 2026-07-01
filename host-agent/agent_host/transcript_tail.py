"""transcript_tail.py — scoped tail of a single Claude Code JSONL transcript.

TranscriptTail watches exactly one (cwd, claude_session_id) transcript file and
forwards new user/assistant turns to an ``emit`` callback. It never scans a
directory and never does blocking file IO on the event loop — all reads go
through ``loop.run_in_executor``. This is the scoped replacement for the old
all-history observer subsystem removed alongside PR #90, which blocked
heartbeats by scanning every transcript file inline.

Two-writer dedup: a headless.prompt drive streams the same turn live over the
websocket. drive_started()/drive_finished() suppress the tail during that
window so the backend doesn't receive the turn twice — see their docstrings.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from agent_host.transcript_paths import claude_transcript_path

log = logging.getLogger(__name__)

# Polling interval in seconds; module-level so tests can monkeypatch it.
POLL_INTERVAL = 1.0

# Lines longer than this are skipped (logged) rather than parsed — guards
# against an unbounded in-memory buffer from a pathological single line.
MAX_LINE_BYTES = 1_000_000


def _extract_text(content) -> str:
    """Extract the authored text from a message.content value.

    ``content`` is either a plain string (typically user turns) or a list of
    content blocks (assistant turns, sometimes user). For a list, join the
    ``text`` field of every block with type "text", in order, separated by
    "\\n\\n". Other block types (tool_use, tool_result, thinking, ...) are
    ignored for text-extraction purposes.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n\n".join(p for p in parts if p)
    return ""


def _is_pure_tool_result(content) -> bool:
    """True if content is a non-empty list where every block is a tool_result."""
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


class TranscriptTail:
    """Tails one Claude Code JSONL transcript and emits new turns."""

    def __init__(self, claude_session_id: str, cwd: str, emit, loop=None) -> None:
        self.claude_session_id = claude_session_id
        self.cwd = cwd
        self._emit = emit
        self._loop = loop
        self._task: asyncio.Task | None = None

        self._path: str | None = None
        self._offset = 0
        self._remainder = b""

        self._suppress = False
        self._buffer: list[dict] = []
        # Bumped on every successful drive_finished; an in-flight _tick read
        # that started under an older epoch discards its data instead of
        # rolling the fast-forwarded offset back and emitting drive output.
        self._epoch = 0

    def start(self) -> None:
        """Create the background polling task.

        Resolves the transcript path synchronously (a cheap stat/glob, not
        worth an executor round-trip) so the EOF-vs-zero starting offset is
        snapshotted at the exact moment watching begins — a writer appending
        a line immediately after start() must not race the first async tick,
        which would otherwise silently fold the new line into the "already
        existed" EOF snapshot and skip it.
        """
        loop = self._loop or asyncio.get_event_loop()
        self._loop = loop

        path = claude_transcript_path(self.cwd, self.claude_session_id)
        if path is not None:
            self._path = path
            try:
                self._offset = os.stat(path).st_size
            except OSError:
                self._offset = 0

        self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def drive_started(self) -> None:
        """Begin suppressing emitted events — a live headless drive is in flight.

        Parsed+filtered events are buffered instead of emitted until
        drive_finished() is called. Safe to call even if already suppressing.
        """
        self._suppress = True

    def drive_finished(self, success: bool) -> None:
        """End suppression for the just-completed headless drive.

        success=True: the live stream already delivered everything correctly.
        Discard the buffer and fast-forward the tail offset to the file's
        current size so nothing written during the drive is replayed.

        success=False: the live stream failed partway; replay the buffered
        events (in order) via emit, then clear the buffer. The offset is left
        alone — normal polling continues from wherever it naturally is.

        Safe to call even if no drive was in progress.
        """
        buffered = self._buffer
        self._buffer = []
        self._suppress = False

        if success:
            self._epoch += 1
            if self._path is not None:
                with contextlib.suppress(OSError):
                    self._offset = os.stat(self._path).st_size
            return

        for event in buffered:
            self._safe_emit(event)

    def _safe_emit(self, event: dict) -> None:
        try:
            self._emit(event)
        except Exception:
            log.debug("transcript_tail: emit raised — ignoring", exc_info=True)

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("transcript_tail: poll cycle failed — continuing", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self) -> None:
        loop = self._loop or asyncio.get_event_loop()

        if self._path is None:
            # File did not exist when start() snapshotted state — keep
            # re-resolving. Once found, begin at offset 0 (nothing existed to
            # skip when watching began), and fall through to read whatever is
            # already in it.
            path = await loop.run_in_executor(
                None, claude_transcript_path, self.cwd, self.claude_session_id
            )
            if path is None:
                return
            self._path = path

        epoch = self._epoch
        size = await loop.run_in_executor(None, self._stat_size, self._path)
        if size is None or size <= self._offset:
            return

        data = await loop.run_in_executor(None, self._read_range, self._path, self._offset, size)
        if data is None:
            return
        if epoch != self._epoch:
            # A drive finished successfully while this read was in flight;
            # drive_finished already fast-forwarded the offset past this data.
            return
        self._offset = size

        self._process_bytes(data)

    def _stat_size(self, path: str) -> int | None:
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def _read_range(self, path: str, start: int, end: int) -> bytes | None:
        try:
            with open(path, "rb") as f:
                f.seek(start)
                return f.read(end - start)
        except OSError:
            return None

    def _process_bytes(self, data: bytes) -> None:
        buf = self._remainder + data
        lines = buf.split(b"\n")
        self._remainder = lines.pop()  # last fragment may be incomplete

        for raw_line in lines:
            if len(raw_line) > MAX_LINE_BYTES:
                log.warning(
                    "transcript_tail: skipping oversized line (%d bytes) in %s",
                    len(raw_line), self._path,
                )
                continue
            self._process_line(raw_line)

    def _process_line(self, raw_line: bytes) -> None:
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            ev = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            log.debug("transcript_tail: non-JSON line — ignoring")
            return
        if not isinstance(ev, dict):
            return

        if ev.get("type") not in ("user", "assistant"):
            return
        uuid = ev.get("uuid")
        if not uuid:
            return
        if ev.get("isMeta"):
            return

        message = ev.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if ev.get("type") == "user" and _is_pure_tool_result(content):
            return

        role_text = _extract_text(content)
        if not role_text.strip():
            return

        event = {
            "role": ev["type"],
            "text": role_text,
            "source_event_key": uuid,
        }

        if self._suppress:
            self._buffer.append(event)
        else:
            self._safe_emit(event)
