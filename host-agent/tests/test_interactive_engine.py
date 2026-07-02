"""Tests for agent_host/interactive_engine.py — InteractiveEngine subprocess manager.

Drives the engine against tests/fake_claude.py (a stand-in claude binary speaking
the stream-json protocol) selected via ORC_CLAUDE_BIN. Behavior of the fake is
controlled by FAKE_CLAUDE_MODE: echo | crash_after_first | tool_step.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from agent_host.interactive_engine import InteractiveEngine

FAKE = os.path.join(os.path.dirname(__file__), "fake_claude.py")


class Collector:
    """Thread-safe collector for engine callbacks."""

    def __init__(self):
        self.events = []
        self.turns = []  # list of is_error bools
        self._turn_done = threading.Event()

    def on_event(self, text):
        self.events.append(text)

    def on_turn_complete(self, is_err):
        self.turns.append(is_err)
        self._turn_done.set()

    def wait_turn(self, timeout=10):
        ok = self._turn_done.wait(timeout)
        self._turn_done.clear()
        return ok


def _make_engine(monkeypatch, mode, collector, cwd=None):
    monkeypatch.setenv("ORC_CLAUDE_BIN", FAKE)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    return InteractiveEngine("fake-sess", cwd or os.getcwd(), collector.on_event, collector.on_turn_complete)


class TestSingleTurnEcho:
    def test_single_turn_echo(self, monkeypatch):
        """
        GIVEN an engine backed by the echo fake
        WHEN send('hello') completes a turn
        THEN an event 'echo:1: hello' is emitted and the turn is not an error.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("hello")
            assert col.wait_turn()
            assert "echo:1: hello" in col.events
            assert col.turns == [False]
        finally:
            engine.stop()


class TestMultiTurnSameProcess:
    def test_multi_turn_same_process(self, monkeypatch):
        """
        GIVEN an engine backed by the echo fake
        WHEN two sends are issued sequentially on one engine
        THEN both turns are served by the SAME process (turn counter 2 on the
        second reply proves no respawn occurred).
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("a")
            assert col.wait_turn()
            engine.send("b")
            assert col.wait_turn()
            assert any(ev == "echo:2: b" for ev in col.events)
        finally:
            engine.stop()


class TestMidTurnSendIsQueued:
    def test_mid_turn_send_is_queued(self, monkeypatch):
        """
        GIVEN an engine with a turn in flight (echo mode)
        WHEN a second send is issued before the first turn completes
        THEN the second input is queued and served after the first, not lost or
        interleaved — both turns succeed and both echoes appear in order.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("x")
            engine.send("y")
            assert col.wait_turn()
            assert col.wait_turn()
            assert col.turns == [False, False]
            assert any(ev == "echo:1: x" for ev in col.events)
            assert any(ev == "echo:2: y" for ev in col.events)
        finally:
            engine.stop()


class TestCrashRespawnsWithResume:
    def test_crash_respawns_with_resume(self, monkeypatch):
        """
        GIVEN an engine whose process crashes after the first turn
        WHEN the process dies and a second send is issued
        THEN a NEW process is spawned with --resume, its turn counter restarts
        at 1, and the turn completes without error.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "crash_after_first", col)
        try:
            engine.send("one")
            assert col.wait_turn()
            # Wait for the crashed process to actually exit.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                proc = engine._proc
                if proc is not None and proc.poll() is not None:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("process did not die within 5s")

            monkeypatch.setenv("FAKE_CLAUDE_MODE", "echo")
            engine.send("two")
            assert col.wait_turn()

            assert col.turns[-1] is False
            assert any(ev == "echo:1: two" for ev in col.events)
            assert "--resume" in engine._proc.args
        finally:
            engine.stop()


class TestToolUseEmitsWrenchStep:
    def test_tool_use_emits_wrench_step(self, monkeypatch):
        """
        GIVEN an engine backed by the tool_step fake
        WHEN a turn completes
        THEN a '🔧 Bash' event (tool_use) and the echo text event are both emitted.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "tool_step", col)
        try:
            engine.send("z")
            assert col.wait_turn()
            assert any("🔧 Bash" in ev for ev in col.events)
            assert any(ev == "echo:1: z" for ev in col.events)
        finally:
            engine.stop()


class TestStopIsIdempotent:
    def test_stop_is_idempotent(self, monkeypatch):
        """
        GIVEN an engine that has completed a turn and been stopped
        WHEN stop() is called a second time and send() is called afterwards
        THEN no exception is raised and no new turn is recorded.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("a")
            assert col.wait_turn()
            engine.stop()
            engine.stop()
            engine.send("after")
            time.sleep(0.5)
            assert len(col.turns) == 1
        finally:
            engine.stop()


def test_started_hint_first_spawn_uses_resume(monkeypatch):
    """
    GIVEN an engine constructed with started=True (session already exists on
          disk — the state after a daemon restart)
    WHEN the first send spawns the process
    THEN the spawn uses --resume, not --session-id (which would make claude
         exit with "Session ID already in use").
    """
    from agent_host.interactive_engine import InteractiveEngine

    monkeypatch.setenv("ORC_CLAUDE_BIN", FAKE)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "echo")
    col = Collector()
    engine = InteractiveEngine(
        "sid-restarted", "", col.on_event, col.on_turn_complete, started=True
    )
    try:
        engine.send("hello again")
        assert col.wait_turn()
        assert "--resume" in engine._proc.args
        assert "--session-id" not in engine._proc.args
        assert col.turns == [False]
    finally:
        engine.stop()
