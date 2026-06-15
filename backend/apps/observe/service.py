"""Observe orchestration and persistence layer.

Manages observed thread creation, session metadata updates, and persisting
parsed transcript turns as thread messages.
"""
from channels.db import database_sync_to_async
from django.conf import settings
from django.db.models import Max

from apps.accounts.models import Account
from apps.observe.runtimes import get_runtime_adapter
from apps.threads.models import Message, Thread

OBSERVER_PROVIDER = settings.OBSERVER_RUNTIME


def get_or_create_observed_thread(session_id, jsonl_path, provider=None) -> Thread:
    runtime = provider or settings.OBSERVER_RUNTIME
    account, _ = Account.objects.get_or_create(
        provider=runtime,
        label="observer",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    existing = Thread.objects.filter(external_session_ref=session_id).first()
    if existing is not None:
        return existing
    meta = get_runtime_adapter(runtime).scan_file_meta(jsonl_path)
    return Thread.objects.create(
        external_session_ref=session_id,
        name=meta.get("title") or f"{runtime}:{session_id[:8]}",
        runtime=runtime,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        observed_jsonl_path=str(jsonl_path),
        account=account,
        metadata={
            "provider": runtime,
            "repo": meta.get("repo", ""),
            "branch": meta.get("branch", ""),
            "title": meta.get("title", ""),
        },
    )


def apply_session_meta(thread, meta) -> bool:
    changed = False
    for key in ("repo", "branch", "title"):
        value = meta.get(key)
        if value and thread.metadata.get(key) != value:
            thread.metadata[key] = value
            changed = True
    if not changed:
        return False
    new_title = meta.get("title")
    if new_title and thread.name != new_title:
        thread.name = new_title
    thread.save(update_fields=["metadata", "name"])
    return True


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
