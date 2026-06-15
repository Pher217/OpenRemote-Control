"""Tests for PTY liveness reconcile on the host-agent side.

Covers:
  - PtySession.list_live_sessions returns names from libtmux
  - PtySession.list_live_sessions propagates exceptions (never returns [] on error)
  - _build_reconcile_frame returns the correct frame when enumeration succeeds
  - _build_reconcile_frame returns None when enumeration fails
  - run_sender sends a session.pty_reconcile frame immediately on connect
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from agent_host.config import HostConfig
from agent_host.queue import OfflineQueue
from agent_host.wsclient import _build_reconcile_frame, run_sender

# ---------------------------------------------------------------------------
# PtySession.list_live_sessions
# ---------------------------------------------------------------------------


def test_list_live_sessions_returns_names(monkeypatch):
    """
    GIVEN a libtmux Server with two sessions
    WHEN list_live_sessions() is called
    THEN it returns a list of those session names.
    """
    from agent_host.pty_session import PtySession

    class FakeSession:
        def __init__(self, name):
            self.name = name

    class FakeServer:
        sessions = [FakeSession("orc-abc"), FakeSession("orc-def")]

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(lambda: FakeServer()))

    result = PtySession().list_live_sessions()
    assert result == ["orc-abc", "orc-def"]


def test_list_live_sessions_empty_when_no_sessions(monkeypatch):
    """
    GIVEN a libtmux Server with no sessions
    WHEN list_live_sessions() is called
    THEN it returns an empty list (legitimate "nothing alive" result).
    """
    from agent_host.pty_session import PtySession

    class FakeServer:
        sessions = []

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(lambda: FakeServer()))

    result = PtySession().list_live_sessions()
    assert result == []


def test_list_live_sessions_propagates_exception(monkeypatch):
    """
    GIVEN a libtmux Server that raises on access (e.g. no tmux binary)
    WHEN list_live_sessions() is called
    THEN the exception propagates — callers must treat this as "unknown", not "nothing alive".
    """
    from agent_host.pty_session import PtySession

    def _boom():
        raise RuntimeError("no tmux server")

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(_boom))

    with pytest.raises(RuntimeError, match="no tmux server"):
        PtySession().list_live_sessions()


# ---------------------------------------------------------------------------
# _build_reconcile_frame
# ---------------------------------------------------------------------------


def test_build_reconcile_frame_returns_correct_frame(monkeypatch):
    """
    GIVEN list_live_sessions returns ["s1", "s2"]
    WHEN _build_reconcile_frame() is called
    THEN it returns {"type": "session.pty_reconcile", "data": {"session_names": ["s1", "s2"]}}.
    """

    class FakeSession:
        def __init__(self, name):
            self.name = name

    class FakeServer:
        sessions = [FakeSession("s1"), FakeSession("s2")]

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(lambda: FakeServer()))

    frame = _build_reconcile_frame()
    assert frame == {
        "type": "session.pty_reconcile",
        "data": {"session_names": ["s1", "s2"]},
    }


def test_build_reconcile_frame_returns_none_on_error(monkeypatch):
    """
    GIVEN list_live_sessions raises (no tmux server)
    WHEN _build_reconcile_frame() is called
    THEN it returns None (fail-safe — never send empty list on error).
    """

    def _boom():
        raise RuntimeError("no tmux")

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(_boom))

    result = _build_reconcile_frame()
    assert result is None


def test_build_reconcile_frame_empty_list_on_no_sessions(monkeypatch):
    """
    GIVEN list_live_sessions returns [] (tmux server exists, zero sessions)
    WHEN _build_reconcile_frame() is called
    THEN it returns a frame with an empty session_names list (legitimate empty reconcile).
    """

    class FakeServer:
        sessions = []

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(lambda: FakeServer()))

    frame = _build_reconcile_frame()
    assert frame is not None
    assert frame["data"]["session_names"] == []


# ---------------------------------------------------------------------------
# run_sender — sends reconcile frame on connect
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        await asyncio.sleep(9999)
        return ""


def test_run_sender_sends_reconcile_on_connect(tmp_path, monkeypatch):
    """
    GIVEN a daemon connecting to the backend and list_live_sessions returns ["sess-1"]
    WHEN run_sender() connects
    THEN a session.pty_reconcile frame is sent immediately (before the first heartbeat).
    """

    class FakeSession:
        def __init__(self, name):
            self.name = name

    class FakeServer:
        sessions = [FakeSession("sess-1")]

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(lambda: FakeServer()))

    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    fake_ws = _FakeWs()
    stop = asyncio.Event()

    @asynccontextmanager
    async def _connect(url):
        stop.set()  # Stop after first connection
        yield fake_ws

    asyncio.run(run_sender(cfg, queue, connect=_connect, stop=stop))

    reconcile_frames = [
        m for m in fake_ws.sent if m.get("type") == "session.pty_reconcile"
    ]
    assert len(reconcile_frames) >= 1
    assert reconcile_frames[0]["data"]["session_names"] == ["sess-1"]


def test_run_sender_skips_reconcile_when_tmux_unavailable(tmp_path, monkeypatch):
    """
    GIVEN list_live_sessions raises (no tmux server)
    WHEN run_sender() connects
    THEN NO session.pty_reconcile frame is sent (fail-safe — never send on error).
    """

    def _boom():
        raise RuntimeError("no tmux")

    monkeypatch.setattr("agent_host.pty_session.PtySession._server", staticmethod(_boom))

    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    fake_ws = _FakeWs()
    stop = asyncio.Event()

    @asynccontextmanager
    async def _connect(url):
        stop.set()
        yield fake_ws

    asyncio.run(run_sender(cfg, queue, connect=_connect, stop=stop))

    reconcile_frames = [
        m for m in fake_ws.sent if m.get("type") == "session.pty_reconcile"
    ]
    assert reconcile_frames == []
