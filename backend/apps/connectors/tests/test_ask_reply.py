"""Tests for resolving an ask_human FREE_TEXT prompt from a typed operator reply.

ask_human delivers a FREE_TEXT prompt to the operator's chat and polls result()
for the answer. resolve_pending_ask turns the operator's typed reply into that
answer so the round-trip completes (previously every ask_human timed out).
"""

import pytest

from apps.connectors.service import ask, register_or_touch, resolve_pending_ask, result
from apps.prompts.models import Prompt


@pytest.mark.django_db
def test_resolve_pending_ask_answers_free_text_prompt():
    """
    GIVEN a pending FREE_TEXT prompt created by ask_human (no options)
    WHEN  resolve_pending_ask is called with the operator's typed reply
    THEN  the prompt is recorded answered with that text and result() returns it.
    """
    nonce = ask(
        connector_id="conn-1",
        tool="claude_code",
        workspace_root="/tmp/ws",
        question="What next?",
        options=[],
    )

    resolved = resolve_pending_ask("run the tests", by="111")

    assert resolved is not None
    assert resolved.nonce == nonce
    assert resolved.status == Prompt.StatusChoices.ANSWERED
    assert resolved.answered_by == "111"
    assert result(nonce) == {"status": "answered", "answer": "run the tests"}


@pytest.mark.django_db
def test_resolve_pending_ask_returns_none_when_no_pending_prompt():
    """
    GIVEN no pending FREE_TEXT prompt exists
    WHEN  resolve_pending_ask is called
    THEN  it returns None so the caller falls back to normal chat dispatch.
    """
    # A connector thread exists but has no pending question.
    register_or_touch("conn-2", "claude_code", "/tmp/ws")

    assert resolve_pending_ask("hello", by="111") is None


@pytest.mark.django_db
def test_resolve_pending_ask_ignores_choice_prompts():
    """
    GIVEN ask_human was called WITH options (a CHOICE_SINGLE prompt, button-answered)
    WHEN  resolve_pending_ask is called
    THEN  it returns None — only options-less FREE_TEXT questions are typed-answered.
    """
    ask(
        connector_id="conn-3",
        tool="claude_code",
        workspace_root="/tmp/ws",
        question="Pick one",
        options=["a", "b"],
    )

    assert resolve_pending_ask("a", by="111") is None


@pytest.mark.django_db
def test_resolve_pending_ask_targets_most_recent_question():
    """
    GIVEN two pending FREE_TEXT questions
    WHEN  resolve_pending_ask is called
    THEN  the most-recently-created one is answered (matches what the operator
          last saw).
    """
    first = ask("conn-4", "claude_code", "/tmp/ws", "first?", [])
    second = ask("conn-4", "claude_code", "/tmp/ws", "second?", [])

    resolved = resolve_pending_ask("answer", by="111")

    assert resolved.nonce == second
    assert result(first) == {"status": "pending"}
    assert result(second) == {"status": "answered", "answer": "answer"}
