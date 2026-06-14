"""Tests for supervisor brain — MockBrain and ToolUseLLM Protocol.

Coverage:
  - MockBrain.chat: returns a dict with 'text' and 'tool_calls' keys
  - MockBrain.chat: deterministic keyed response on 'empty fleet' substring
  - MockBrain.chat: deterministic keyed response on 'needs input' substring
  - MockBrain.chat: default response for unknown content
  - MockBrain.chat: case-insensitive matching
  - MockBrain: satisfies ToolUseLLM Protocol at runtime
"""

from __future__ import annotations

import asyncio

import pytest

from apps.supervisor.brain import MockBrain, ToolUseLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


def _user_msg(content: str) -> dict:
    return {"role": "user", "content": content}


# ---------------------------------------------------------------------------
# ToolUseLLM Protocol
# ---------------------------------------------------------------------------


def test_mock_brain_satisfies_tool_use_llm_protocol():
    """
    GIVEN a MockBrain instance
    WHEN checked against the ToolUseLLM Protocol
    THEN isinstance returns True.
    """
    brain = MockBrain()
    assert isinstance(brain, ToolUseLLM)


# ---------------------------------------------------------------------------
# MockBrain.chat — return shape
# ---------------------------------------------------------------------------


def test_mock_brain_returns_text_and_tool_calls_keys():
    """
    GIVEN a MockBrain and any message
    WHEN chat is called
    THEN the result dict has exactly 'text' and 'tool_calls' keys.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("hello")], []))
    assert "text" in result
    assert "tool_calls" in result


def test_mock_brain_tool_calls_is_list():
    """
    GIVEN a MockBrain and any message
    WHEN chat is called
    THEN tool_calls is a list.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("hello")], []))
    assert isinstance(result["tool_calls"], list)


def test_mock_brain_text_is_str():
    """
    GIVEN a MockBrain and any message
    WHEN chat is called
    THEN text is a str.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("hello")], []))
    assert isinstance(result["text"], str)


# ---------------------------------------------------------------------------
# MockBrain.chat — deterministic keyed responses
# ---------------------------------------------------------------------------


def test_mock_brain_empty_fleet_response():
    """
    GIVEN a message containing 'empty fleet'
    WHEN MockBrain.chat is called
    THEN the response text is the canned 'empty fleet' reply.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("The empty fleet has no sessions.")], []))
    assert result["text"] == "No active sessions."


def test_mock_brain_needs_input_response():
    """
    GIVEN a message containing 'needs input'
    WHEN MockBrain.chat is called
    THEN the response text is the canned 'needs input' reply.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("Session alpha needs input from you.")], []))
    assert "waiting for your approval" in result["text"]


def test_mock_brain_default_response():
    """
    GIVEN a message that does not match any keyed response
    WHEN MockBrain.chat is called
    THEN the default response text is returned.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("Something completely unrelated.")], []))
    assert result["text"] == "Fleet digest: all sessions running normally."


def test_mock_brain_case_insensitive_matching():
    """
    GIVEN a message with 'EMPTY FLEET' in upper case
    WHEN MockBrain.chat is called
    THEN the keyed response still matches (case-insensitive).
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("EMPTY FLEET STATUS")], []))
    assert result["text"] == "No active sessions."


def test_mock_brain_uses_first_user_message():
    """
    GIVEN a message list with a system message followed by a user message
    WHEN MockBrain.chat is called
    THEN matching is based on the user message content, not the system message.
    """
    brain = MockBrain()
    messages = [
        {"role": "system", "content": "You are a supervisor."},
        {"role": "user", "content": "empty fleet today"},
    ]
    result = _run(brain.chat(messages, []))
    assert result["text"] == "No active sessions."


def test_mock_brain_no_user_message_returns_default():
    """
    GIVEN a message list with no user-role message
    WHEN MockBrain.chat is called
    THEN the default response is returned.
    """
    brain = MockBrain()
    messages = [{"role": "system", "content": "System only."}]
    result = _run(brain.chat(messages, []))
    assert result["text"] == "Fleet digest: all sessions running normally."


def test_mock_brain_accepts_empty_tools_list():
    """
    GIVEN an empty tools list
    WHEN MockBrain.chat is called
    THEN it does not raise and returns a valid response.
    """
    brain = MockBrain()
    result = _run(brain.chat([_user_msg("hello")], []))
    assert "text" in result


def test_mock_brain_accepts_non_empty_tools_list():
    """
    GIVEN a non-empty tools list
    WHEN MockBrain.chat is called
    THEN it does not raise (tools are ignored by MockBrain).
    """
    brain = MockBrain()
    tools = [{"name": "fleet.read_state", "description": "Read fleet state"}]
    result = _run(brain.chat([_user_msg("hello")], tools))
    assert "text" in result
