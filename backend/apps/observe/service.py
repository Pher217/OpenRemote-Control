from channels.db import database_sync_to_async
from django.db.models import Max

from apps.accounts.models import Account
from apps.threads.models import Message, Thread

OBSERVER_PROVIDER = "claude_code"


def get_or_create_observed_thread(session_id, jsonl_path) -> Thread:
    account, _ = Account.objects.get_or_create(
        provider=OBSERVER_PROVIDER,
        label="observer",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    thread, _ = Thread.objects.get_or_create(
        external_session_ref=session_id,
        defaults={
            "name": f"claude_code:{session_id[:8]}",
            "runtime": "claude_code",
            "runtime_mode": Thread.RuntimeModeChoices.OBSERVED,
            "observed_jsonl_path": str(jsonl_path),
            "account": account,
        },
    )
    return thread


@database_sync_to_async
def record_turn(thread, role, text) -> Message:
    nxt = (
        Message.objects.filter(thread=thread).aggregate(m=Max("sequence"))["m"] or 0
    ) + 1
    return Message.objects.create(
        thread=thread,
        role=role if role in {"user", "assistant"} else "system",
        redacted_content=text,
        sequence=nxt,
        metadata={},
    )
