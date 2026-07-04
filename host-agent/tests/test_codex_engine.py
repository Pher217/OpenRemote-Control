"""Tests for agent_host/codex_engine.py — CodexEngine per-turn subprocess driver.

Drives the engine against tests/fake_codex.py (an executable stand-in for the
codex binary speaking the --json event protocol) selected via ORC_CODEX_BIN.
The fake's behavior is controlled by FAKE_CODEX_MODE: echo | crash | tool_step.
"""
from __future__ import annotations

import os
import threading

from agent_host.codex_engine import CodexEngine

FAKE = os.path.join(os.path.dirname(__file__), "fake_codex.py")


class Collector:
    def __init__(self):
        self.events = []
        self.turns = []
        self._d = threading.Event()

    def on_event(self, t):
        self.events.append(t)

    def on_turn_complete(self, err):
        self.turns.append(err)
        self._d.set()

    def wait(self, timeout=10):
        ok = self._d.wait(timeout)
        self._d.clear()
        return ok


def _make_engine(monkeypatch, mode, collector):
    monkeypatch.setenv("ORC_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    return CodexEngine(os.getcwd(), collector.on_event, collector.on_turn_complete)


class TestSingleTurnEcho:
    def test_single_turn_echo(self, monkeypatch):
        """
        GIVEN an engine backed by the echo fake
        WHEN send('hello') completes a turn
        THEN 'echo:hello' is forwarded as an event and the turn is not an error.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("hello")
            assert col.wait()
            assert "echo:hello" in col.events
            assert col.turns == [False]
        finally:
            engine.stop()


class TestMultiTurnResumesSameSession:
    def test_multi_turn_resumes_same_session(self, monkeypatch):
        """
        GIVEN an engine backed by the echo fake
        WHEN two sends are issued sequentially on one engine
        THEN both prompts are echoed, both turns are non-error, and the second
        turn used the resume path so the fake reported the resumed thread id.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("a")
            assert col.wait()
            engine.send("b")
            assert col.wait()
            assert "echo:a" in col.events
            assert "echo:b" in col.events
            assert col.turns == [False, False]
            assert engine._session_id == "codex-resumed"
        finally:
            engine.stop()


class TestMidTurnSendIsQueued:
    def test_mid_turn_send_is_queued(self, monkeypatch):
        """
        GIVEN an engine with a turn in flight (echo mode)
        WHEN a second send is issued before the first turn completes
        THEN the second input is queued and served after the first, both turns
        succeed, and both echoes appear.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "echo", col)
        try:
            engine.send("x")
            engine.send("y")
            assert col.wait()
            assert col.wait()
            assert col.turns == [False, False]
            assert "echo:x" in col.events
            assert "echo:y" in col.events
        finally:
            engine.stop()


class TestToolStepItemNotForwardedAsText:
    def test_tool_step_item_not_forwarded_as_text(self, monkeypatch):
        """
        GIVEN an engine backed by the tool_step fake
        WHEN a turn completes
        THEN the agent_message is forwarded ('echo:z' is present) but the
        non-agent_message command_execution item is NOT forwarded (no event
        equals 'ls' and no event contains 'command_execution').
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "tool_step", col)
        try:
            engine.send("z")
            assert col.wait()
            assert "echo:z" in col.events
            assert "ls" not in col.events
            assert not any("command_execution" in ev for ev in col.events)
        finally:
            engine.stop()


class TestCrashMarksTurnError:
    def test_crash_marks_turn_error(self, monkeypatch):
        """
        GIVEN an engine backed by the crash fake (process dies without
        emitting turn.completed)
        WHEN send('boom') is issued
        THEN the turn completes with is_error True on process death.
        """
        col = Collector()
        engine = _make_engine(monkeypatch, "crash", col)
        try:
            engine.send("boom")
            assert col.wait()
            assert col.turns == [True]
        finally:
            engine.stop()
