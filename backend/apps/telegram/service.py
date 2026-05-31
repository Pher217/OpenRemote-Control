from channels.db import database_sync_to_async
from django.conf import settings

from apps.accounts.models import Account
from apps.telegram.models import TelegramChat
from apps.threads.dispatch import dispatch_text
from apps.threads.models import Thread


def get_or_create_thread_for_chat(chat_id) -> Thread:
    existing = (
        TelegramChat.objects.select_related("thread", "thread__account")
        .filter(chat_id=chat_id)
        .first()
    )
    if existing is not None:
        return existing.thread

    account, _ = Account.objects.get_or_create(
        provider="ollama",
        label="telegram",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    thread = Thread.objects.create(
        name=f"telegram:{chat_id}",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={"model": settings.TELEGRAM_DEFAULT_MODEL},
    )
    TelegramChat.objects.create(chat_id=chat_id, thread=thread)
    return thread


@database_sync_to_async
def _get_thread_with_account(thread_id) -> Thread:
    return Thread.objects.select_related("account").get(id=thread_id)


async def handle_update(chat_id: int, text: str, *, send):
    if chat_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        return

    thread = await database_sync_to_async(get_or_create_thread_for_chat)(chat_id)
    thread = await _get_thread_with_account(thread.id)

    buffer = ""
    reply = ""

    async def on_event(data):
        nonlocal buffer, reply
        etype = data.get("type")
        if etype == "message_delta":
            buffer += data.get("text", "")
        elif etype == "message_complete":
            reply = data.get("text") or buffer
        elif etype == "slash_result":
            reply = data.get("message", "")
        elif etype == "error":
            reply = f"⚠️ {data.get('message', '')}"

    await dispatch_text(thread, text, on_event=on_event)

    if reply:
        await send(chat_id, reply)
