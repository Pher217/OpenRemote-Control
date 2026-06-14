"""Tests for supervisor brain — MockBrain, ToolUseLLM Protocol, OllamaBrain,
and the summarise_fleet helper.

Coverage:
  - MockBrain.chat: returns a dict with 'text' and 'tool_calls' keys
  - MockBrain.chat: deterministic keyed response on 'empty fleet' substring
  - MockBrain.chat: deterministic keyed response on 'needs input' substring
  - MockBrain.chat: default response for unknown content
  - MockBrain.chat: case-insensitive matching
  - MockBrain: satisfies ToolUseLLM Protocol at runtime
  - summarise_fleet: returns brain text, model-free (MockBrain)
  - OllamaBrain.chat: raises NotImplementedError when tools is non-empty (S3 boundary)
  - OllamaBrain.chat: live Ollama call (opt-in via RUN_OLLAMA_LIVE=1)
"""

from __future__ import annotations

import asyncio
import os

import pytest

from apps.supervisor.brain import MockBrain, OllamaBrain, ToolUseLLM, summarise_fleet


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


# ---------------------------------------------------------------------------
# summarise_fleet — model-free path (MockBrain)
# ---------------------------------------------------------------------------


def test_summarise_fleet_returns_string():
    """
    GIVEN a MockBrain and a non-empty digest_text
    WHEN summarise_fleet is called
    THEN the result is a non-empty string.
    """
    brain = MockBrain()
    result = _run(summarise_fleet(brain, "Session A is running normally."))
    assert isinstance(result, str)
    assert result  # non-empty


def test_summarise_fleet_uses_brain_text():
    """
    GIVEN a MockBrain with a deterministic 'empty fleet' response
    WHEN summarise_fleet is called with digest_text containing 'empty fleet'
    THEN the returned string is the canned MockBrain reply.
    """
    brain = MockBrain()
    result = _run(summarise_fleet(brain, "empty fleet — no sessions active"))
    assert result == "No active sessions."


def test_summarise_fleet_default_response():
    """
    GIVEN a MockBrain and generic digest text (no keyed phrase)
    WHEN summarise_fleet is called
    THEN the returned string is the MockBrain default reply.
    """
    brain = MockBrain()
    result = _run(summarise_fleet(brain, "Session X is compiling code."))
    assert result == "Fleet digest: all sessions running normally."


# ---------------------------------------------------------------------------
# OllamaBrain — read-only boundary guard (model-free)
# ---------------------------------------------------------------------------


def test_ollama_brain_raises_on_non_empty_tools():
    """
    GIVEN an OllamaBrain and a non-empty tools list
    WHEN chat is called
    THEN NotImplementedError is raised (tool-calling is S3, not S1.3).
    """
    brain = OllamaBrain()
    tools = [{"name": "fleet.run", "description": "Launch a session"}]
    with pytest.raises(NotImplementedError, match="tool-calling is S3"):
        _run(brain.chat([_user_msg("do something")], tools))


def test_ollama_brain_empty_tools_does_not_raise_guard():
    """
    GIVEN an OllamaBrain and an empty tools list
    WHEN chat is called (will fail at network level in CI — tested separately)
    THEN NotImplementedError is NOT raised by the guard.

    Note: this test only verifies the guard is not triggered; the network call
    will fail in CI without Ollama running, which is expected and acceptable here
    because we are only testing the guard path (tools=[]).  The live call test is
    in the RUN_OLLAMA_LIVE block below.
    """
    brain = OllamaBrain(base_url="http://127.0.0.1:1")  # unreachable port
    with pytest.raises(Exception) as exc_info:
        _run(brain.chat([_user_msg("hello")], []))
    # The exception must NOT be NotImplementedError — the guard was not triggered.
    assert not isinstance(exc_info.value, NotImplementedError)


def test_ollama_brain_satisfies_tool_use_llm_protocol():
    """
    GIVEN an OllamaBrain instance
    WHEN checked against the ToolUseLLM Protocol
    THEN isinstance returns True.
    """
    brain = OllamaBrain()
    assert isinstance(brain, ToolUseLLM)


# ---------------------------------------------------------------------------
# OllamaBrain — live Ollama call (opt-in, skipped by default)
# ---------------------------------------------------------------------------

pytestmark_live = pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_LIVE") != "1",
    reason="live Ollama test; set RUN_OLLAMA_LIVE=1 to run",
)


@pytestmark_live
def test_ollama_brain_live_chat():
    """
    GIVEN a live local Ollama instance with the default model
    WHEN OllamaBrain.chat is called with a simple prompt and empty tools
    THEN a non-empty text reply is returned and tool_calls is an empty list.

    Skipped unless RUN_OLLAMA_LIVE=1 is set (mirrors apps/tier2/tests/test_ollama.py).
    """
    brain = OllamaBrain()
    result = _run(brain.chat([_user_msg("Reply with exactly one word: hello.")], []))
    assert isinstance(result, dict)
    assert isinstance(result["text"], str)
    assert result["text"].strip()
    assert result["tool_calls"] == []


@pytestmark_live
def test_ollama_brain_live_summarise_fleet():
    """
    GIVEN a live local Ollama instance
    WHEN summarise_fleet is called with OllamaBrain and a short digest
    THEN a non-empty string is returned.

    Skipped unless RUN_OLLAMA_LIVE=1 is set.
    """
    brain = OllamaBrain()
    digest = (
        "Session 'claude-main': running — last step: wrote tests — no input needed.\n"
        "Session 'codex-fix': waiting_approval — last step: proposed fix — NEEDS INPUT."
    )
    result = _run(summarise_fleet(brain, digest))
    assert isinstance(result, str)
    assert result.strip()
