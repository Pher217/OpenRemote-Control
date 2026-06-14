"""
ToolUseLLM Protocol and MockBrain for testing.

Real impl (LiteLLM → kimi-k2.6:cloud or gemma4:31b) is the S1.2 follow-up
per spec Track S §S1.2.  This module defines the contract that the digest
loop (S1.3) and future supervisor code will depend on.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolUseLLM(Protocol):
    """Provider-agnostic interface for a tool-use capable LLM.

    Both the real kimi/gemma brain and the test MockBrain satisfy this protocol.
    The digest loop always receives a ToolUseLLM and never inspects the concrete
    type — this is the Safety Contract #2 boundary (brain output is untrusted).
    """

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """Send a conversation turn and return the brain's response.

        Args:
            messages: OpenAI-compatible message list, e.g.
                      [{"role": "user", "content": "..."}]
            tools:    OpenAI-compatible tool definitions (may be empty).

        Returns:
            {
              "text":       str — the model's text reply (may be ""),
              "tool_calls": list[dict] — zero or more tool-call dicts,
                            each {"name": str, "arguments": dict},
            }
        """
        ...


class MockBrain:
    """Deterministic stand-in for ToolUseLLM — used in tests.

    Responses are keyed on the first user message content substring so tests
    can probe different branches without network/GPU access.

    Satisfies the ToolUseLLM Protocol.
    """

    _RESPONSES: dict[str, dict] = {
        "empty fleet": {
            "text": "No active sessions.",
            "tool_calls": [],
        },
        "needs input": {
            "text": "Session alpha is waiting for your approval.",
            "tool_calls": [],
        },
    }
    _DEFAULT: dict = {
        "text": "Fleet digest: all sessions running normally.",
        "tool_calls": [],
    }

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Return a canned response based on the first user message content.

        Matching is case-insensitive substring search so tests can use natural
        phrases as keys without exact-string fragility.
        """
        first_user = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"),
            "",
        )
        lower = first_user.lower()
        for key, response in self._RESPONSES.items():
            if key in lower:
                return dict(response)
        return dict(self._DEFAULT)
