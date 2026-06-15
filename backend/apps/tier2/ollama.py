"""Ollama provider adapter for the Tier-2 chat model app.

Streams chat completions from a local Ollama `/api/chat` endpoint and yields
normalized message-delta and message-complete events.
"""

import json

import httpx

from apps.tier2.base import NormalizedEvent, register_adapter


@register_adapter
class OllamaAdapter:
    provider = "ollama"

    async def stream(self, thread, history):
        base_url = (
            (getattr(thread.account, "metadata", None) or {}).get("base_url")
            or "http://localhost:11434"
        ).rstrip("/")
        model = (
            (thread.metadata or {}).get("model")
            or (getattr(thread.account, "metadata", None) or {}).get("model")
            or "gemma4:31b"
        )
        payload = {"model": model, "messages": history, "stream": True}
        url = f"{base_url}/api/chat"
        full = ""

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)
            ) as client, client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = (obj.get("message") or {}).get("content", "")
                    if chunk:
                        full += chunk
                        yield NormalizedEvent(
                            kind="message_delta", payload={"text": chunk}
                        )
                    if obj.get("done"):
                        yield NormalizedEvent(
                            kind="message_complete", payload={"text": full}
                        )
                        return
        except httpx.HTTPError as exc:
            yield NormalizedEvent(kind="error", payload={"message": str(exc)})
            return
