"""
Tests for daemon.py — _poll_loop oversized-event handling.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from agent_host.config import HostConfig
from agent_host.daemon import _poll_loop
from agent_host.queue import OfflineQueue
from agent_host.tailer import OffsetStore
from agent_host.wsclient import MAX_EVENT_BYTES


def _make_stop_after_one_iteration() -> asyncio.Event:
    """Return a stop event that is set immediately (so _poll_loop runs once)."""
    ev = asyncio.Event()
    ev.set()
    return ev


@pytest.mark.asyncio
async def test_poll_loop_truncates_oversized_line(tmp_path):
    """
    GIVEN a JSONL file containing a line whose serialized event would exceed MAX_EVENT_BYTES
    WHEN _poll_loop() processes it
    THEN the enqueued event's raw field is truncated so the event JSON is under the limit.
    """
    big_line = "Z" * (MAX_EVENT_BYTES + 500_000)

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    offsets = OffsetStore()
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()

    call_count = 0

    def _iter_files_once(provider):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ["/tmp/fake_session.jsonl"]
        stop.set()  # Stop after second call to iter_files (second poll iteration).
        return []

    with (
        patch("agent_host.daemon.iter_files", side_effect=_iter_files_once),
        patch(
            "agent_host.daemon.read_new_lines",
            return_value=([big_line + "\n"], len(big_line) + 1),
        ),
    ):
        await _poll_loop(cfg, queue, ["claude_code"], offsets, 0.0, stop)

    # The queue must contain exactly one event (truncated, not skipped).
    assert len(queue) == 1, "Expected one (truncated) event in the queue"

    events = queue._read_all()
    assert len(events) == 1
    event = events[0]

    # The enqueued event must be valid JSON under the limit.
    encoded = json.dumps(event).encode("utf-8")
    assert len(encoded) <= MAX_EVENT_BYTES, (
        f"Enqueued event still too large: {len(encoded)} bytes"
    )

    # The raw field must be truncated (not the full original).
    raw = event["data"]["raw"]
    assert len(raw) < len(big_line), "raw field was not truncated"
    assert "[truncated]" in raw, "truncated marker must be present"


@pytest.mark.asyncio
async def test_poll_loop_does_not_enqueue_tiny_oversized_line(tmp_path):
    """
    GIVEN a JSONL line whose overhead alone exceeds MAX_EVENT_BYTES
    (i.e. even an empty raw field would exceed the limit)
    WHEN _poll_loop() processes it
    THEN the event is skipped entirely (not enqueued).
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    offsets = OffsetStore()
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()

    # Use a path so long that the overhead alone exceeds the limit.
    huge_path = "p" * MAX_EVENT_BYTES  # path alone exceeds the limit
    normal_line = "hello"

    # Manually build an event and check it'd exceed the limit even with empty raw.
    dummy_event = {
        "type": "session.line",
        "data": {
            "provider": "claude_code",
            "jsonl_path": huge_path,
            "raw": "",
        },
    }
    overhead = len(json.dumps(dummy_event).encode("utf-8"))
    assert overhead > MAX_EVENT_BYTES, "Test setup: overhead alone must exceed the limit"

    call_count = 0

    def _iter_files_once(provider):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [huge_path]
        stop.set()
        return []

    with (
        patch("agent_host.daemon.iter_files", side_effect=_iter_files_once),
        patch(
            "agent_host.daemon.read_new_lines",
            return_value=([normal_line + "\n"], len(normal_line) + 1),
        ),
    ):
        await _poll_loop(cfg, queue, ["claude_code"], offsets, 0.0, stop)

    # The event must be skipped entirely (queue empty).
    assert len(queue) == 0, (
        "Expected empty queue when event overhead alone exceeds MAX_EVENT_BYTES"
    )


@pytest.mark.asyncio
async def test_poll_loop_normal_events_are_enqueued(tmp_path):
    """
    GIVEN a normal-sized JSONL line
    WHEN _poll_loop() processes it
    THEN it is enqueued unchanged.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    offsets = OffsetStore()
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()

    normal_line = '{"role": "user", "content": "hello"}'

    call_count = 0

    def _iter_files_once(provider):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ["/tmp/session.jsonl"]
        stop.set()
        return []

    with (
        patch("agent_host.daemon.iter_files", side_effect=_iter_files_once),
        patch(
            "agent_host.daemon.read_new_lines",
            return_value=([normal_line + "\n"], len(normal_line) + 1),
        ),
    ):
        await _poll_loop(cfg, queue, ["claude_code"], offsets, 0.0, stop)

    assert len(queue) == 1
    events = queue._read_all()
    assert events[0]["data"]["raw"] == normal_line
