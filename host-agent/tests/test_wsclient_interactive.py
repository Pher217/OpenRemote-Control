"""Tests for the ORC_HEADLESS_ENGINE=interactive routing in wsclient.

The interactive engine keeps ONE persistent fake-claude process per session;
headless.prompt turns must route to it (no per-turn respawn), stream events
into the outbound queue with the same data-wrapped frames as the default
path, and respect the tail-suppression contract.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import agent_host.wsclient as wsclient_module
from agent_host.wsclient import handle_host_command

FAKE = os.path.join(os.path.dirname(__file__), "fake_claude.py")


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    monkeypatch.setenv("ORC_CLAUDE_BIN", FAKE)
    monkeypatch.setenv("ORC_HEADLESS_ENGINE", "interactive")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "echo")
    wsclient_module._engines.clear()
    wsclient_module._headless_locks.clear()
    yield
    for engine in list(wsclient_module._engines.values()):
        engine.stop()
    wsclient_module._engines.clear()
    wsclient_module._headless_locks.clear()


def _prompt_frame(text, sid="sess-int-1"):
    return {
        "type": "host_command",
        "command": "headless.prompt",
        "claude_session_id": sid,
        "thread_id": "thread-int-1",
        "cwd": "",
        "text": text,
        "started": False,
    }


async def _drain_replies(outbound, want, timeout=15.0):
    """Collect session.headless_reply frames until `want` texts seen or timeout."""
    texts = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(texts) < want:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(outbound.get(), timeout=remaining)
        except TimeoutError:
            break
        if frame.get("type") == "session.headless_reply":
            texts.append(frame["data"]["text"])
    return texts


@pytest.mark.asyncio
async def test_interactive_mode_routes_to_persistent_engine():
    """
    GIVEN ORC_HEADLESS_ENGINE=interactive and two headless.prompt frames
    WHEN both are handled
    THEN both replies arrive via session.headless_reply frames AND the second
         reply carries the fake's turn counter 2 — proving ONE process served
         both turns (no per-turn respawn).
    """
    outbound: asyncio.Queue = asyncio.Queue()

    handle_host_command(_prompt_frame("alpha"), incoming_queue=outbound)
    first = await _drain_replies(outbound, want=1)
    assert first and first[0] == "echo:1: alpha"

    handle_host_command(_prompt_frame("beta"), incoming_queue=outbound)
    second = await _drain_replies(outbound, want=1)
    assert second and second[0] == "echo:2: beta"

    assert len(wsclient_module._engines) == 1


@pytest.mark.asyncio
async def test_interactive_mode_suppresses_tail_during_turn():
    """
    GIVEN an armed transcript tail for the session
    WHEN an interactive turn runs
    THEN drive_started is called before the turn and drive_finished(success=True)
         after it — same suppression contract as the per-turn path.
    """
    calls = []

    class FakeTail:
        cwd = ""

        def drive_started(self):
            calls.append("started")

        def drive_finished(self, success):
            calls.append(f"finished:{success}")

    outbound: asyncio.Queue = asyncio.Queue()
    wsclient_module._transcript_tails["sess-int-1"] = FakeTail()
    try:
        handle_host_command(_prompt_frame("gamma"), incoming_queue=outbound)
        replies = await _drain_replies(outbound, want=1)
        assert replies == ["echo:1: gamma"]
        # drive_finished runs in the handler's finally — give it a tick.
        await asyncio.sleep(0.05)
        assert calls == ["started", "finished:True"]
    finally:
        del wsclient_module._transcript_tails["sess-int-1"]
