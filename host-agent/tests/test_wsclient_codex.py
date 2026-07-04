"""Tests for the provider="codex" routing in wsclient.

The Codex engine drives a per-session Codex process keyed by thread_id;
headless.prompt turns with provider="codex" route to it (one engine reused
across turns for the same thread_id), stream agent_message text back as
session.headless_reply data-wrapped frames, and a missing thread_id is
ignored without creating an engine.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import agent_host.wsclient as wsclient_module
from agent_host.wsclient import handle_host_command

FAKE = os.path.join(os.path.dirname(__file__), "fake_codex.py")


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    monkeypatch.setenv("ORC_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_MODE", "echo")
    wsclient_module._codex_engines.clear()
    wsclient_module._headless_locks.clear()
    yield
    for engine in list(wsclient_module._codex_engines.values()):
        engine.stop()
    wsclient_module._codex_engines.clear()
    wsclient_module._headless_locks.clear()


def _prompt_frame(text, thread_id="thread-cdx-1", cwd="/tmp"):
    return {
        "type": "host_command",
        "command": "headless.prompt",
        "provider": "codex",
        "thread_id": thread_id,
        "cwd": cwd,
        "text": text,
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
async def test_codex_provider_routes_to_codex_engine():
    """
    GIVEN two headless.prompt frames with provider="codex" and the same thread_id
    WHEN both are handled
    THEN both replies arrive via session.headless_reply frames (echo:<text>)
         AND one Codex engine serves both turns (no per-turn respawn).
    """
    outbound: asyncio.Queue = asyncio.Queue()

    handle_host_command(_prompt_frame("alpha"), incoming_queue=outbound)
    first = await _drain_replies(outbound, want=1)
    assert first and first[0] == "echo:alpha"

    handle_host_command(_prompt_frame("beta"), incoming_queue=outbound)
    second = await _drain_replies(outbound, want=1)
    assert second and second[0] == "echo:beta"

    assert len(wsclient_module._codex_engines) == 1


@pytest.mark.asyncio
async def test_codex_missing_thread_id_is_ignored():
    """
    GIVEN a headless.prompt frame with provider="codex" and an empty thread_id
    WHEN it is handled
    THEN no Codex engine is created and no reply is enqueued.
    """
    outbound: asyncio.Queue = asyncio.Queue()

    handle_host_command(_prompt_frame("x", thread_id=""), incoming_queue=outbound)
    await asyncio.sleep(0.1)

    assert wsclient_module._codex_engines == {}
    assert outbound.empty()
