"""
ToolUseLLM Protocol, MockBrain (test stand-in), OllamaBrain (real impl), and
the summarise_fleet helper used by the S1.3 digest loop.

S1.3 scope: read-only digest only (text-in → text-out).  Tool/function calling
is S3 (managed actions, security-gated) and is NOT implemented here.  Passing a
non-empty ``tools`` list to OllamaBrain raises NotImplementedError to make the
read-only boundary explicit in code rather than silently ignoring it.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import httpx

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# MockBrain — deterministic test stand-in
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# OllamaBrain — real Ollama impl, S1.3 read-only digest only
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "gemma4:31b"

# Timeouts mirror apps/tier2/ollama.py conventions.
_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)


class OllamaBrain:
    """Real brain implementation backed by a local Ollama instance.

    Implements the ToolUseLLM Protocol for S1.3 read-only digest use.

    Tool/function calling is S3 scope (managed actions, security-gated).
    Passing a non-empty ``tools`` list raises NotImplementedError so that
    callers fail loudly instead of silently dropping tool definitions.

    Config is resolved (in priority order):
      1. Constructor ``base_url`` / ``model`` arguments.
      2. Environment variables ``SUPERVISOR_OLLAMA_BASE_URL`` / ``SUPERVISOR_BRAIN_MODEL``.
      3. Hard-coded defaults (localhost:11434 / gemma4:31b).

    No new pip dependencies — httpx is already used by apps/tier2/ollama.py.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = (
            base_url
            or os.environ.get("SUPERVISOR_OLLAMA_BASE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = (
            model
            or os.environ.get("SUPERVISOR_BRAIN_MODEL")
            or _DEFAULT_MODEL
        )

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Send messages to Ollama and return text reply.

        Args:
            messages: OpenAI-compatible message list.
            tools:    Must be empty for S1.3.  Non-empty raises NotImplementedError
                      because tool-calling is S3 and explicitly out of scope.

        Returns:
            {"text": <reply str>, "tool_calls": []}

        Raises:
            NotImplementedError: if ``tools`` is non-empty.
            httpx.HTTPError: on network or HTTP-level failure.
        """
        if tools:
            raise NotImplementedError(
                "tool-calling is S3, not implemented"
            )  # line 145 — read-only boundary guard

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        url = f"{self._base_url}/api/chat"

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()

        data = resp.json()
        text = (data.get("message") or {}).get("content", "")
        return {"text": text, "tool_calls": []}


# ---------------------------------------------------------------------------
# summarise_fleet — thin helper for the S1.3 digest loop
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a terse fleet supervisor. "
    "Summarise what the coding sessions are doing. "
    "For each active session give one line: what it is doing now, its last major step, "
    "and whether it needs operator input. "
    "Flag sessions that are waiting, blocked, or stuck. "
    "Be brief — no preamble, no sign-off."
)


async def summarise_fleet(brain: ToolUseLLM, digest_text: str) -> str:
    """Ask ``brain`` to summarise a pre-rendered fleet digest.

    This is the entry point that the S1.3 cadence loop will call.  It builds
    a minimal system + user message pair and returns the brain's text reply.

    Args:
        brain:       Any ToolUseLLM implementor (MockBrain in tests, OllamaBrain
                     in production).
        digest_text: The fleet state rendered as plain text (produced by the
                     digest layer — this function does not call fleet.read_state
                     itself; callers supply the text).

    Returns:
        The brain's natural-language summary as a plain string.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": digest_text},
    ]
    result = await brain.chat(messages, tools=[])
    return result["text"]
