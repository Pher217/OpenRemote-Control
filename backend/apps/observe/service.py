"""Turn persistence layer.

Persists assistant/user turns as thread messages. Shared by the driveable
headless/PTY pipeline (apps.hostlink) — the read-only observe ingestion that
used to live here has been removed.
"""
from channels.db import database_sync_to_async
from django.db import IntegrityError, transaction

from apps.threads.models import Message


@database_sync_to_async
def record_turn(thread, role, text, source=None, *, source_event_key: str | None = None) -> Message | None:
    nxt = (
        Message.objects.filter(thread=thread)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
        or 0
    ) + 1
    try:
        with transaction.atomic():
            return Message.objects.create(
                thread=thread,
                role=role if role in {"user", "assistant"} else "system",
                redacted_content=text,
                sequence=nxt,
                metadata={"source": source} if source else {},
                source_event_key=source_event_key,
            )
    except IntegrityError:
        if source_event_key is None:
            raise
        return None
