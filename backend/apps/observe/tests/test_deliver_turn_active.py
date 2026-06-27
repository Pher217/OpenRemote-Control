"""Tests for deliver_turn_active — the platform-agnostic delivery entry point."""
import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.observe.delivery import deliver_turn_active
from apps.threads.models import Thread


def _make_thread(session_id, jsonl_path="", provider="claude_code", **meta):
    account, _ = Account.objects.get_or_create(
        provider=provider,
        label="test",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    return Thread.objects.create(
        external_session_ref=session_id,
        name=meta.get("title") or f"{provider}:{session_id[:8]}",
        runtime=provider,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        observed_jsonl_path=str(jsonl_path),
        account=account,
        metadata={
            "provider": provider,
            "repo": meta.get("repo", ""),
            "branch": meta.get("branch", ""),
            "title": meta.get("title", ""),
        },
    )


class _FakeTelegramApi:
    def __init__(self):
        self._next_id = 2000
        self.create_calls = []
        self.send_calls = []

    async def create_forum_topic(self, chat_id, name, icon_color):
        self.create_calls.append((chat_id, name, icon_color))
        topic_id = self._next_id
        self._next_id += 1
        return topic_id

    async def send_message(
        self,
        chat_id,
        text,
        message_thread_id=None,
        parse_mode=None,
        reply_markup=None,
        disable_notification=None,
    ):
        self.send_calls.append((chat_id, text, message_thread_id, parse_mode))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_telegram_active_routes_through_telegram_path(settings):
    """
    GIVEN Telegram is the active platform with a forum chat id configured
    WHEN deliver_turn_active is called
    THEN the Telegram forum path is used (topic created, message sent) and no GatewayMessage is created.
    """
    settings.ORC_MESSAGING_PLATFORM = "telegram"
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.ORC_PROMPT_CHAT_ID = ""

    fake = _FakeTelegramApi()
    thread = await database_sync_to_async(_make_thread)(
        "Stg00001", "/tmp/tg1.jsonl"
    )
    turn = {"role": "user", "text": "hello telegram", "uuid": "t1", "session_id": "Stg00001"}

    await deliver_turn_active(thread, turn, None, api=fake)

    assert len(fake.create_calls) == 1, "Expected exactly one forum topic to be created"
    assert len(fake.send_calls) >= 2, "Expected intro + turn messages sent"

    from apps.gateway.models import GatewayMessage
    count = await database_sync_to_async(GatewayMessage.objects.count)()
    assert count == 0, "No GatewayMessage should be created for Telegram delivery"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_whatsapp_active_creates_gateway_message(settings):
    """
    GIVEN WhatsApp is the active platform with a recipient configured
    WHEN deliver_turn_active is called
    THEN exactly one GatewayMessage is created for platform='whatsapp' with a session-label prefix;
    and the Telegram API is NOT called.
    """
    settings.ORC_MESSAGING_PLATFORM = "whatsapp"
    settings.ORC_PROMPT_WHATSAPP = "+41791234567"
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.ORC_PROMPT_CHAT_ID = ""

    thread = await database_sync_to_async(_make_thread)(
        "Swa00001", "/tmp/wa1.jsonl"
    )
    turn = {"role": "user", "text": "hello whatsapp", "uuid": "w1", "session_id": "Swa00001"}

    fake = _FakeTelegramApi()
    await deliver_turn_active(thread, turn, None, api=fake)

    assert len(fake.create_calls) == 0, "Telegram API must not be called for WhatsApp"
    assert len(fake.send_calls) == 0, "Telegram API must not be called for WhatsApp"

    from apps.gateway.models import GatewayMessage

    messages = await database_sync_to_async(
        lambda: list(GatewayMessage.objects.filter(platform="whatsapp"))
    )()
    assert len(messages) == 1, "Expected exactly one GatewayMessage"
    msg = messages[0]
    assert msg.recipient == "+41791234567"
    assert msg.text.startswith("["), "Message text must start with the '[label]' prefix"
    assert "hello whatsapp" in msg.text


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_recipient_configured_is_noop(settings):
    """
    GIVEN no recipient is configured on any platform
    WHEN deliver_turn_active is called
    THEN it returns without creating a GatewayMessage and without raising.
    """
    settings.ORC_MESSAGING_PLATFORM = "telegram"
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.ORC_PROMPT_CHAT_ID = ""

    thread = await database_sync_to_async(_make_thread)(
        "Snoop001", "/tmp/noop1.jsonl"
    )
    turn = {"role": "user", "text": "silent", "uuid": "n1", "session_id": "Snoop001"}

    fake = _FakeTelegramApi()
    await deliver_turn_active(thread, turn, None, api=fake)

    assert len(fake.create_calls) == 0
    assert len(fake.send_calls) == 0

    from apps.gateway.models import GatewayMessage

    count = await database_sync_to_async(GatewayMessage.objects.count)()
    assert count == 0
