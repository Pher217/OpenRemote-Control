"""Inbound message dispatch for the Thread primitive.

Routes text to slash commands or to the thread's tier2 provider adapter,
persists messages, builds conversation history, and streams back deltas,
completions, and errors.
"""
from channels.db import database_sync_to_async

from apps.slash.handlers import get_handler
from apps.slash.parser import parse
from apps.threads.models import Message, Thread


async def dispatch_text(thread, text, *, on_event, extra_system_context: str | None = None):
    if not (text or "").strip():
        await on_event({"type": "error", "message": "empty message"})
        return

    parsed = parse(text)

    if parsed[0] == "slash":
        cmd, args = parsed[1], parsed[2]
        await _persist_message(thread, "slash", text)
        handler = get_handler(cmd)
        if handler is None:
            await on_event(
                {
                    "type": "slash_result",
                    "ok": False,
                    "message": f"Unknown command: /{cmd}",
                }
            )
            return
        result = await database_sync_to_async(handler)(thread, args)
        refreshed = await _get_thread(thread.id)
        if refreshed is not None:
            thread = refreshed
        await on_event({"type": "slash_result", **result})
        return

    await _persist_message(thread, "user", text)
    history = await _build_history(thread)

    # Prepend ephemeral fleet context as a system message when provided.
    # This message is NOT persisted to the DB — it lives only for this call.
    if extra_system_context:
        history = [{"role": "system", "content": extra_system_context}] + history

    from apps.tier2.base import UnknownProviderError, get_adapter

    try:
        adapter = get_adapter(thread.account.provider)
    except UnknownProviderError:
        await on_event(
            {
                "type": "error",
                "message": f"No adapter for provider {thread.account.provider}",
            }
        )
        return

    full = ""
    async for ev in adapter.stream(thread, history):
        if ev.kind == "message_delta":
            chunk = ev.payload.get("text", "")
            full += chunk
            await on_event({"type": "message_delta", "text": chunk})
        elif ev.kind == "message_complete":
            full = ev.payload.get("text") or full
            msg = await _persist_message(thread, "assistant", full)
            await on_event(
                {
                    "type": "message_complete",
                    "text": full,
                    "sequence": msg.sequence,
                    "message_id": str(msg.id),
                }
            )
        elif ev.kind == "error":
            await on_event({"type": "error", "message": ev.payload.get("message", "")})
            break


@database_sync_to_async
def _get_thread(thread_id):
    try:
        return Thread.objects.select_related("account").get(id=thread_id)
    except Thread.DoesNotExist:
        return None


@database_sync_to_async
def _persist_message(thread, role, text):
    from django.db.models import Max

    nxt = (
        Message.objects.filter(thread=thread).aggregate(m=Max("sequence"))["m"] or 0
    ) + 1
    return Message.objects.create(
        thread=thread, role=role, redacted_content=text, sequence=nxt
    )


@database_sync_to_async
def _build_history(thread):
    allowed = {"user", "assistant", "system"}
    return [
        {"role": m.role, "content": m.redacted_content}
        for m in Message.objects.filter(thread=thread).order_by("sequence")
        if m.role in allowed
    ]
