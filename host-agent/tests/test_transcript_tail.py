"""Tests for agent_host.transcript_tail.TranscriptTail.

Invariants verified:
  - Parser/filter: assistant text emitted, user string content emitted,
    pure tool_result user events skipped, isMeta skipped, missing uuid
    skipped, empty extracted text skipped.
  - Tail starts at EOF (no backfill of pre-existing content).
  - A line appended after start is emitted.
  - A partial line is only emitted once completed.
  - An oversized line (>1MB) is skipped but the next valid line still emits.
  - Suppression: drive_started buffers; drive_finished(True) discards the
    buffer and fast-forwards the offset to EOF; drive_finished(False) replays
    the buffer in order.

All tests use SYNTHETIC JSONL content and monkeypatch claude_transcript_path
to tmp_path fixtures — never the real ~/.claude directory.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_host import transcript_tail as tt_module
from agent_host.transcript_tail import TranscriptTail, _extract_text, _is_pure_tool_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assistant_event(uuid: str, text: str, is_meta: bool = False) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "isMeta": is_meta,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _user_str_event(uuid: str, text: str, is_meta: bool = False) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "isMeta": is_meta,
        "message": {"role": "user", "content": text},
    }


def _user_tool_result_event(uuid: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    }


def _line(ev: dict) -> bytes:
    return (json.dumps(ev) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Parser / filter unit tests (no file IO — direct calls into private helpers
# and _process_line via a minimally constructed tail)
# ---------------------------------------------------------------------------


def _make_tail(emitted: list) -> TranscriptTail:
    return TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)


def test_extract_text_from_string_content():
    """GIVEN plain string content WHEN extracted THEN returned as-is."""
    assert _extract_text("hello world") == "hello world"


def test_extract_text_joins_text_blocks():
    """GIVEN a list of content blocks with text/tool_use WHEN extracted
    THEN only text blocks are joined with a blank line."""
    content = [
        {"type": "text", "text": "first"},
        {"type": "tool_use", "name": "Bash"},
        {"type": "text", "text": "second"},
    ]
    assert _extract_text(content) == "first\n\nsecond"


def test_is_pure_tool_result_true_for_all_tool_result_blocks():
    """GIVEN a content list where every block is tool_result THEN True."""
    content = [{"type": "tool_result", "content": "x"}]
    assert _is_pure_tool_result(content) is True


def test_is_pure_tool_result_false_when_mixed():
    """GIVEN a content list with a non-tool_result block THEN False."""
    content = [{"type": "tool_result", "content": "x"}, {"type": "text", "text": "y"}]
    assert _is_pure_tool_result(content) is False


def test_assistant_text_event_is_emitted():
    """
    GIVEN an assistant event with a text block
    WHEN processed
    THEN it is emitted with role, text, and source_event_key set from uuid.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = _assistant_event("uuid-1", "hello from assistant")
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert len(emitted) == 1
    assert emitted[0] == {
        "role": "assistant",
        "text": "hello from assistant",
        "source_event_key": "uuid-1",
    }


def test_user_string_content_event_is_emitted():
    """
    GIVEN a user event whose content is a plain string
    WHEN processed
    THEN it is emitted.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = _user_str_event("uuid-2", "hi claude")
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert len(emitted) == 1
    assert emitted[0]["role"] == "user"
    assert emitted[0]["text"] == "hi claude"
    assert emitted[0]["source_event_key"] == "uuid-2"


def test_user_pure_tool_result_event_is_skipped():
    """
    GIVEN a user event whose content is only tool_result blocks
    WHEN processed
    THEN nothing is emitted.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = _user_tool_result_event("uuid-3")
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert emitted == []


def test_is_meta_event_is_skipped():
    """
    GIVEN an assistant event with isMeta=True
    WHEN processed
    THEN nothing is emitted.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = _assistant_event("uuid-4", "meta text", is_meta=True)
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert emitted == []


def test_missing_uuid_event_is_skipped():
    """
    GIVEN an assistant event with no uuid field
    WHEN processed
    THEN nothing is emitted.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = _assistant_event("uuid-5", "text")
    del ev["uuid"]
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert emitted == []


def test_empty_extracted_text_is_skipped():
    """
    GIVEN an assistant event whose content has no text blocks (e.g. pure
    tool_use)
    WHEN processed
    THEN nothing is emitted.
    """
    emitted: list = []
    tail = _make_tail(emitted)
    ev = {
        "type": "assistant",
        "uuid": "uuid-6",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]},
    }
    tail._process_line(_line(ev).rstrip(b"\n"))

    assert emitted == []


# ---------------------------------------------------------------------------
# Tail behavior tests — real tmp files on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_transcript_path(monkeypatch, tmp_path):
    """Point claude_transcript_path at a controllable tmp file path."""
    transcript_file = tmp_path / "transcript.jsonl"

    def fake_path(cwd, session_id):
        return str(transcript_file) if transcript_file.exists() else None

    monkeypatch.setattr(tt_module, "claude_transcript_path", fake_path)
    monkeypatch.setattr(tt_module, "POLL_INTERVAL", 0.01)
    return transcript_file


@pytest.mark.asyncio
async def test_starts_at_eof_no_backfill(patch_transcript_path):
    """
    GIVEN a transcript file that already has content when start() is called
    WHEN the tail starts
    THEN the pre-existing content is NOT emitted.
    """
    transcript_file = patch_transcript_path
    transcript_file.write_bytes(_line(_assistant_event("pre-1", "old content")))

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)
        assert emitted == []
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_line_appended_after_start_is_emitted(patch_transcript_path):
    """
    GIVEN a tail already running against an existing file
    WHEN a new line is appended
    THEN it is emitted.
    """
    transcript_file = patch_transcript_path
    transcript_file.write_bytes(_line(_assistant_event("pre-1", "old content")))

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)
        with open(transcript_file, "ab") as f:
            f.write(_line(_assistant_event("new-1", "new content")))
        await asyncio.sleep(0.1)

        assert len(emitted) == 1
        assert emitted[0]["text"] == "new content"
        assert emitted[0]["source_event_key"] == "new-1"
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_partial_line_emitted_only_once_complete(patch_transcript_path):
    """
    GIVEN a tail running against a file that does not exist yet
    WHEN a line is written in two chunks (partial, then completed)
    THEN the event is emitted exactly once, after the line is completed.
    """
    transcript_file = patch_transcript_path
    # File doesn't exist at start() time.

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)

        full_line = _line(_assistant_event("split-1", "split content"))
        half = len(full_line) // 2
        transcript_file.write_bytes(full_line[:half])
        await asyncio.sleep(0.05)
        assert emitted == []  # not complete yet

        with open(transcript_file, "ab") as f:
            f.write(full_line[half:])
        await asyncio.sleep(0.1)

        assert len(emitted) == 1
        assert emitted[0]["text"] == "split content"
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_oversized_line_skipped_next_valid_line_emitted(patch_transcript_path):
    """
    GIVEN a tail running against a new file
    WHEN a line larger than 1MB is written, followed by a valid line
    THEN the oversized line is skipped and the next valid line is emitted.
    """
    transcript_file = patch_transcript_path

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)

        huge_text = "x" * (tt_module.MAX_LINE_BYTES + 1000)
        huge_line = _line(_assistant_event("huge-1", huge_text))
        valid_line = _line(_assistant_event("valid-1", "small content"))
        transcript_file.write_bytes(huge_line + valid_line)
        await asyncio.sleep(0.1)

        assert len(emitted) == 1
        assert emitted[0]["source_event_key"] == "valid-1"
        assert emitted[0]["text"] == "small content"
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_drive_finished_success_discards_buffer_and_advances_offset(patch_transcript_path):
    """
    GIVEN a tail in suppression mode with buffered events
    WHEN drive_finished(success=True) is called
    THEN the buffer is discarded and the offset advances to EOF, so content
         written during suppression is never replayed.
    """
    transcript_file = patch_transcript_path

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)

        tail.drive_started()
        with open(transcript_file, "ab") as f:
            f.write(_line(_assistant_event("suppressed-1", "suppressed content")))
        await asyncio.sleep(0.1)

        assert emitted == []  # buffered, not emitted
        assert tail._buffer  # something was buffered

        tail.drive_finished(success=True)
        assert tail._buffer == []
        assert emitted == []

        # Offset now at EOF — polling further ticks should not re-emit anything.
        await asyncio.sleep(0.1)
        assert emitted == []
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_drive_finished_failure_replays_buffer_in_order(patch_transcript_path):
    """
    GIVEN a tail in suppression mode with multiple buffered events
    WHEN drive_finished(success=False) is called
    THEN the buffered events are emitted in original order.
    """
    transcript_file = patch_transcript_path

    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.start()
    try:
        await asyncio.sleep(0.05)

        tail.drive_started()
        with open(transcript_file, "ab") as f:
            f.write(_line(_assistant_event("buf-1", "first")))
            f.write(_line(_user_str_event("buf-2", "second")))
        await asyncio.sleep(0.1)

        assert emitted == []

        tail.drive_finished(success=False)

        assert len(emitted) == 2
        assert emitted[0]["text"] == "first"
        assert emitted[1]["text"] == "second"
        assert tail._buffer == []
    finally:
        await tail.stop()


@pytest.mark.asyncio
async def test_drive_started_and_finished_idempotent_without_active_drive(patch_transcript_path):
    """
    GIVEN a tail that never had drive_started() called
    WHEN drive_finished() is called anyway
    THEN it does not raise.
    """
    emitted: list = []
    tail = TranscriptTail("sess-1", "/tmp/proj", emit=emitted.append)
    tail.drive_finished(success=True)
    tail.drive_finished(success=False)
