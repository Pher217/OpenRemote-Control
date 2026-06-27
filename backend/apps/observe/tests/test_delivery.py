import httpx
import pytest
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.accounts.models import Account
from apps.observe.delivery import TELEGRAM_MAX, _topic_name, deliver_turn, pick_color
from apps.observe.validators import VALID_DELIVERY_MODES, validate_observe_delivery_mode
from apps.telegram.telegram_api import FORUM_ICON_COLORS
from apps.threads.models import Thread

_cache_clear = sync_to_async(cache.clear)


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


def _apply_meta(thread, meta):
    changed = False
    for key in ("repo", "branch", "title"):
        value = meta.get(key)
        if value and thread.metadata.get(key) != value:
            thread.metadata[key] = value
            changed = True
    if changed:
        new_title = meta.get("title")
        if new_title and thread.name != new_title:
            thread.name = new_title
        thread.save(update_fields=["metadata", "name"])
    return changed


class _FakeApi:
    def __init__(self):
        self._next_msg_id = 100
        self._next_id = 1000
        self.create_calls = []
        self.send_calls = []
        self.send_kwargs = []
        self.edit_calls = []  # list of (chat_id, message_id, text, kwargs)
        self._edit_ok = True  # set False to simulate edit failure

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
        disable_notification=None,
    ) -> int:
        self.send_calls.append((chat_id, text, message_thread_id, parse_mode))
        self.send_kwargs.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
        )
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        return msg_id

    async def edit_message_text(
        self,
        chat_id,
        message_id,
        text,
        *,
        message_thread_id=None,
        parse_mode=None,
    ) -> bool:
        self.edit_calls.append(
            (chat_id, message_id, text, {"message_thread_id": message_thread_id})
        )
        return self._edit_ok


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_creates_one_topic_per_session_and_routes():
    """GIVEN two sessions WHEN turns delivered THEN one topic per session, routed."""
    fake = _FakeApi()
    thread_a = await database_sync_to_async(_make_thread)(
        "Saaaaaaa", "/tmp/a.jsonl"
    )
    thread_b = await database_sync_to_async(_make_thread)(
        "Sbbbbbbb", "/tmp/b.jsonl"
    )

    turn_a1 = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Saaaaaaa"}
    turn_a2 = {"role": "assistant", "text": "yo", "uuid": "2", "session_id": "Saaaaaaa"}
    turn_b1 = {"role": "user", "text": "hey", "uuid": "3", "session_id": "Sbbbbbbb"}

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread_a, turn_a1, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread_a, turn_a2, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread_b, turn_b1, None, forum_chat_id=-100999, api=fake)

    assert len(fake.create_calls) == 2
    for _chat_id, _name, color in fake.create_calls:
        assert color in FORUM_ICON_COLORS

    @database_sync_to_async
    def _topic(thread_id):
        return Thread.objects.get(id=thread_id).metadata["telegram_topic_id"]

    topic_a = await _topic(thread_a.id)
    topic_b = await _topic(thread_b.id)
    assert topic_a is not None
    assert topic_b is not None
    assert topic_a != topic_b

    for _chat_id, text, _topic, parse_mode in fake.send_calls:
        if parse_mode == "HTML":
            # session intro / user milestone messages are HTML
            assert text.startswith("<b>")
        # else: progress digest sends are plain text (accumulated transcript) — no HTML

    a_sends = [c for c in fake.send_calls if c[2] == topic_a]
    b_sends = [c for c in fake.send_calls if c[2] == topic_b]
    # one intro + the turns per topic
    assert len(a_sends) == 3
    assert len(b_sends) == 2
    a_intro, a_turns = a_sends[0], a_sends[1:]
    b_intro, b_turns = b_sends[0], b_sends[1:]
    assert "session" in a_intro[1]
    assert "session" in b_intro[1]
    assert a_turns[0][1].endswith("hi")
    assert a_turns[1][1].endswith("yo")
    assert b_turns[0][1].endswith("hey")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_truncates_long_text():
    """GIVEN text over the limit WHEN delivered THEN it is truncated with an ellipsis."""
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Slooooong", "/tmp/l.jsonl"
    )
    turn = {
        "role": "assistant",
        "text": "x" * (TELEGRAM_MAX + 500),
        "uuid": "1",
        "session_id": "Slooooong",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="all"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    sent_text = fake.send_calls[-1][1]
    assert len(sent_text) <= TELEGRAM_MAX
    assert sent_text.endswith("…")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_falls_back_to_plain_text_on_html_send_failure():
    """GIVEN the HTML send raises WHEN delivered THEN a plain-text send follows."""

    class _FailingHtmlApi(_FakeApi):
        async def send_message(
            self,
            chat_id,
            text,
            message_thread_id=None,
            parse_mode=None,
            disable_notification=None,
        ):
            if parse_mode == "HTML":
                raise RuntimeError("can't parse entities")
            self.send_calls.append((chat_id, text, message_thread_id, parse_mode))
            self.send_kwargs.append(
                {
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_notification": disable_notification,
                }
            )
            return self._next_msg_id

    fake = _FailingHtmlApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sfallbck", "/tmp/f.jsonl"
    )

    @database_sync_to_async
    def _pre_create_topic():
        thread.metadata["telegram_topic_id"] = 4242
        thread.save(update_fields=["metadata"])

    await _pre_create_topic()
    turn = {
        "role": "user",
        "text": "<bad html",
        "uuid": "1",
        "session_id": "Sfallbck",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    assert len(fake.send_calls) == 1
    _chat_id, text, _topic, parse_mode = fake.send_calls[0]
    assert parse_mode is None
    assert text == "You: <bad html"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_topic_name_and_intro_from_meta():
    """GIVEN a thread with meta WHEN a topic is created THEN name is prov·repo·title
    and a single HTML intro precedes the turn; a later turn reuses the topic."""
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Smeta123", "/tmp/m.jsonl"
    )
    await database_sync_to_async(_apply_meta)(
        thread,
        {"repo": "agent-command-center", "branch": "claude/x", "title": "My Title"},
    )

    assert _topic_name(thread) == "👁 claude_code · agent-command-center · My Title"
    assert len(_topic_name(thread)) <= 128

    turn1 = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Smeta123"}
    turn2 = {"role": "assistant", "text": "yo", "uuid": "2", "session_id": "Smeta123"}

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread, turn1, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread, turn2, None, forum_chat_id=-100999, api=fake)

    assert len(fake.create_calls) == 1
    _chat_id, name, _color = fake.create_calls[0]
    assert name == "👁 claude_code · agent-command-center · My Title"

    intro, parse_mode = fake.send_calls[0][1], fake.send_calls[0][3]
    assert parse_mode == "HTML"
    assert "<code>agent-command-center</code>" in intro
    assert "<b>My Title</b>" in intro
    assert "session <code>Smeta123</code>" in intro

    intros = [c for c in fake.send_calls if "session <code>" in c[1]]
    assert len(intros) == 1
    assert len(fake.send_calls) == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_intro_notifies_user_turn_notifies():
    """
    GIVEN a new observed session in progress mode
    WHEN the first (user) turn is delivered
    THEN the topic-intro message notifies and the user turn also notifies.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Ssilent1", "/tmp/silent.jsonl"
    )
    turn = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Ssilent1"}

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    # First send is the intro (default notification, disable_notification omitted → None).
    assert fake.send_kwargs[0]["disable_notification"] is None
    # User turn is a milestone — it notifies (disable_notification=False, not True/None).
    assert fake.send_kwargs[-1]["disable_notification"] is False


def test_pick_color_deterministic():
    """GIVEN a session id WHEN picked twice THEN same valid palette color each time."""
    c1 = pick_color("Saaaaaaa")
    c2 = pick_color("Saaaaaaa")
    assert c1 == c2
    assert c1 in FORUM_ICON_COLORS


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_save_topic_id_persists_forum_chat_id():
    """
    GIVEN a new observed thread delivered into a forum
    WHEN  deliver_turn creates the topic
    THEN  metadata contains telegram_forum_chat_id alongside telegram_topic_id.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sforum01", "/tmp/fc.jsonl"
    )
    turn = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Sforum01"}

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100777, api=fake)

    @database_sync_to_async
    def _meta():
        t = Thread.objects.get(id=thread.id)
        return t.metadata.get("telegram_forum_chat_id"), t.metadata.get("telegram_topic_id")

    forum_id, topic_id = await _meta()
    assert topic_id is not None
    assert forum_id == -100777


# ── New tests for progress-mode digest behaviour ──────────────────────────────


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_progress_mode_collapses_assistant_turns_into_digest():
    """
    GIVEN progress mode and 3 consecutive assistant turns
    WHEN all three turns are delivered
    THEN ONE initial send + TWO edits (not 3 sends); digest message_id stored in metadata.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sprogr01", "/tmp/p1.jsonl"
    )

    turns = [
        {"role": "assistant", "text": f"step {i}", "uuid": str(i), "session_id": "Sprogr01"}
        for i in range(3)
    ]

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        for t in turns:
            await deliver_turn(thread, t, None, forum_chat_id=-100999, api=fake)

    # 1 topic create, 1 intro send, 1 initial digest send = 2 sends total.
    # (intro is send_calls[0], digest is send_calls[1])
    assert len(fake.send_calls) == 2
    # 2 edits for turns 2 and 3
    assert len(fake.edit_calls) == 2

    @database_sync_to_async
    def _digest_id():
        return Thread.objects.get(id=thread.id).metadata.get("telegram_digest_message_id")

    stored_id = await _digest_id()
    assert stored_id is not None
    # The stored id must be the id returned by the initial send.
    assert stored_id == fake.send_calls[1][0] or stored_id == 101  # intro=100, digest=101
    # Verify edit was called with the stored digest message id.
    first_edit_msg_id = fake.edit_calls[0][1]
    assert first_edit_msg_id == stored_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_progress_mode_user_turn_notifies_and_resets_digest():
    """
    GIVEN progress mode with an active assistant digest
    WHEN a user turn is delivered
    THEN a fresh notifying message is posted and telegram_digest_message_id is cleared.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sprogr02", "/tmp/p2.jsonl"
    )

    assistant_turn = {
        "role": "assistant",
        "text": "thinking...",
        "uuid": "1",
        "session_id": "Sprogr02",
    }
    user_turn = {
        "role": "user",
        "text": "next question",
        "uuid": "2",
        "session_id": "Sprogr02",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(thread, assistant_turn, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread, user_turn, None, forum_chat_id=-100999, api=fake)

    # The user turn send must have disable_notification=False.
    user_turn_kwargs = fake.send_kwargs[-1]
    assert user_turn_kwargs["disable_notification"] is False

    # Digest metadata must be cleared after the user turn.
    @database_sync_to_async
    def _meta():
        t = Thread.objects.get(id=thread.id)
        return (
            t.metadata.get("telegram_digest_message_id"),
            t.metadata.get("telegram_digest_steps"),
        )

    digest_id, digest_steps = await _meta()
    assert digest_id is None
    assert digest_steps is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_progress_mode_next_assistant_after_user_starts_fresh_digest():
    """
    GIVEN progress mode, a user turn (which clears the digest), then an assistant turn
    WHEN the assistant turn is delivered
    THEN a brand-new send is made (not an edit), and a new digest id is stored.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sprogr03", "/tmp/p3.jsonl"
    )

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(
            thread,
            {"role": "user", "text": "hi", "uuid": "1", "session_id": "Sprogr03"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )
        sends_after_user = len(fake.send_calls)
        await deliver_turn(
            thread,
            {"role": "assistant", "text": "reply", "uuid": "2", "session_id": "Sprogr03"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )

    # One more send for the new digest (no edit).
    assert len(fake.send_calls) == sends_after_user + 1
    assert len(fake.edit_calls) == 0

    @database_sync_to_async
    def _digest_id():
        return Thread.objects.get(id=thread.id).metadata.get("telegram_digest_message_id")

    assert await _digest_id() is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_milestones_only_drops_assistant_turns():
    """
    GIVEN milestones_only mode
    WHEN assistant turns and a user turn are delivered
    THEN assistant turns are dropped (no send beyond intro); user turn posts a fresh message.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Smilest1", "/tmp/ms1.jsonl"
    )

    with override_settings(OBSERVE_DELIVERY_MODE="milestones_only"):
        await deliver_turn(
            thread,
            {"role": "assistant", "text": "thinking", "uuid": "1", "session_id": "Smilest1"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )
        await deliver_turn(
            thread,
            {"role": "assistant", "text": "still thinking", "uuid": "2", "session_id": "Smilest1"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )
        await deliver_turn(
            thread,
            {"role": "user", "text": "hello", "uuid": "3", "session_id": "Smilest1"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )

    # Only intro + user turn = 2 sends; no edits.
    assert len(fake.send_calls) == 2
    assert len(fake.edit_calls) == 0
    # The last send is the user turn (milestone), which notifies.
    assert fake.send_kwargs[-1]["disable_notification"] is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_all_mode_sends_each_assistant_turn_silently():
    """
    GIVEN all (legacy) mode
    WHEN three assistant turns are delivered
    THEN each turn posts its own silent message (no edits).
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sallmod1", "/tmp/al1.jsonl"
    )

    with override_settings(OBSERVE_DELIVERY_MODE="all"):
        for i in range(3):
            await deliver_turn(
                thread,
                {
                    "role": "assistant",
                    "text": f"turn {i}",
                    "uuid": str(i),
                    "session_id": "Sallmod1",
                },
                None,
                forum_chat_id=-100999,
                api=fake,
            )

    # intro + 3 assistant sends = 4 sends; no edits.
    assert len(fake.send_calls) == 4
    assert len(fake.edit_calls) == 0
    # All assistant sends are silent.
    for kwargs in fake.send_kwargs[1:]:  # skip intro
        assert kwargs["disable_notification"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_send_message_returns_id_and_edit_uses_it():
    """
    GIVEN progress mode and two consecutive assistant turns
    WHEN the second assistant turn is delivered
    THEN edit_message_text is called with the message_id returned by the first send.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sretid01", "/tmp/ri1.jsonl"
    )

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(
            thread,
            {"role": "assistant", "text": "first", "uuid": "1", "session_id": "Sretid01"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )
        # At this point intro was send_calls[0] (msg_id=100), digest was send_calls[1] (msg_id=101).

        @database_sync_to_async
        def _stored_digest_id():
            return Thread.objects.get(id=thread.id).metadata.get("telegram_digest_message_id")

        stored = await _stored_digest_id()
        assert stored is not None  # send_message returned an int id

        await deliver_turn(
            thread,
            {"role": "assistant", "text": "second", "uuid": "2", "session_id": "Sretid01"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )

    # edit_message_text must have been called once, with the stored digest id.
    assert len(fake.edit_calls) == 1
    _chat_id, edit_msg_id, _text, _kwargs = fake.edit_calls[0]
    assert edit_msg_id == stored


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_progress_mode_edit_failure_falls_back_to_fresh_send():
    """
    GIVEN progress mode and an active digest whose edit fails
    WHEN the next assistant turn is delivered
    THEN a fresh message is sent and the stored digest id is updated.
    """
    fake = _FakeApi()
    fake._edit_ok = False  # simulate edit failure (message too old / deleted)
    thread = await database_sync_to_async(_make_thread)(
        "Seditfl1", "/tmp/ef1.jsonl"
    )

    @database_sync_to_async
    def _get_digest_id():
        return Thread.objects.get(id=thread.id).metadata.get("telegram_digest_message_id")

    with override_settings(OBSERVE_DELIVERY_MODE="progress"):
        await deliver_turn(
            thread,
            {"role": "assistant", "text": "first", "uuid": "1", "session_id": "Seditfl1"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )
        first_stored = await _get_digest_id()

        await deliver_turn(
            thread,
            {"role": "assistant", "text": "second", "uuid": "2", "session_id": "Seditfl1"},
            None,
            forum_chat_id=-100999,
            api=fake,
        )

    # Edit was attempted but failed → a new send happened.
    assert len(fake.edit_calls) == 1
    assert fake.edit_calls[0][1] == first_stored
    # Total sends: intro + first digest + fallback digest = 3.
    assert len(fake.send_calls) == 3

    @database_sync_to_async
    def _new_stored():
        return Thread.objects.get(id=thread.id).metadata.get("telegram_digest_message_id")

    new_stored = await _new_stored()
    # Digest id updated to the new message.
    assert new_stored is not None
    assert new_stored != first_stored


# ── Tests for delivery mode validation and idempotency guard ────────────────────


def test_invalid_observe_delivery_mode_raises():
    """
    GIVEN an invalid OBSERVE_DELIVERY_MODE value
    WHEN validated
    THEN ImproperlyConfigured is raised with a clear message.
    """
    with pytest.raises(ImproperlyConfigured) as exc_info:
        validate_observe_delivery_mode("invalid_mode")
    assert "invalid_mode" in str(exc_info.value)
    assert "OBSERVE_DELIVERY_MODE" in str(exc_info.value)


def test_valid_observe_delivery_modes_pass():
    """
    GIVEN valid OBSERVE_DELIVERY_MODE values
    WHEN validated
    THEN no exception is raised.
    """
    for mode in VALID_DELIVERY_MODES:
        validate_observe_delivery_mode(mode)  # should not raise


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_same_turn_delivered_twice_sends_once():
    """
    GIVEN a turn delivered once
    WHEN the same turn is delivered again within 30s
    THEN only one telegram send is made.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sidemp01", "/tmp/id1.jsonl"
    )

    @database_sync_to_async
    def _pre_create_topic():
        thread.metadata["telegram_topic_id"] = 4242
        thread.save(update_fields=["metadata"])

    await _pre_create_topic()
    await _cache_clear()

    turn = {
        "role": "assistant",
        "text": "hello",
        "uuid": "same-uuid-123",
        "session_id": "Sidemp01",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="all"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    # Only one send for the turn (no intro because topic pre-created)
    assert len(fake.send_calls) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_different_turns_delivered_twice_sends_twice():
    """
    GIVEN two different turns
    WHEN both are delivered
    THEN two telegram sends are made.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(_make_thread)(
        "Sidemp02", "/tmp/id2.jsonl"
    )

    @database_sync_to_async
    def _pre_create_topic():
        thread.metadata["telegram_topic_id"] = 4242
        thread.save(update_fields=["metadata"])

    await _pre_create_topic()
    await _cache_clear()

    turn1 = {
        "role": "assistant",
        "text": "hello",
        "uuid": "uuid-1",
        "session_id": "Sidemp02",
    }
    turn2 = {
        "role": "assistant",
        "text": "world",
        "uuid": "uuid-2",
        "session_id": "Sidemp02",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="all"):
        await deliver_turn(thread, turn1, None, forum_chat_id=-100999, api=fake)
        await deliver_turn(thread, turn2, None, forum_chat_id=-100999, api=fake)

    assert len(fake.send_calls) == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stale_topic_is_recreated_and_redelivered():
    """
    GIVEN an observed thread whose telegram_topic_id points at a deleted forum topic
    WHEN a turn is delivered and the send to that stale topic raises Telegram's
         400 'message thread not found'
    THEN the stale topic state is cleared, a fresh topic is created, and the turn
         is redelivered to the new topic.
    """

    class _StaleTopicApi(_FakeApi):
        def __init__(self, stale_topic_id):
            super().__init__()
            self._stale_topic_id = stale_topic_id

        async def send_message(
            self,
            chat_id,
            text,
            message_thread_id=None,
            parse_mode=None,
            disable_notification=None,
        ):
            if message_thread_id == self._stale_topic_id:
                req = httpx.Request(
                    "POST", "https://api.telegram.org/botX/sendMessage"
                )
                resp = httpx.Response(
                    400,
                    json={
                        "ok": False,
                        "error_code": 400,
                        "description": "Bad Request: message thread not found",
                    },
                    request=req,
                )
                raise httpx.HTTPStatusError("msg", request=req, response=resp)
            return await super().send_message(
                chat_id,
                text,
                message_thread_id=message_thread_id,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
            )

    fake = _StaleTopicApi(stale_topic_id=999)
    thread = await database_sync_to_async(_make_thread)(
        "Sstale01", "/tmp/stale.jsonl"
    )

    @database_sync_to_async
    def _pre_set_stale_topic():
        thread.metadata["telegram_topic_id"] = 999
        thread.save(update_fields=["metadata"])

    await _pre_set_stale_topic()
    await _cache_clear()

    turn = {
        "role": "assistant",
        "text": "hello",
        "uuid": "stale-uuid-1",
        "session_id": "Sstale01",
    }

    with override_settings(OBSERVE_DELIVERY_MODE="all"):
        await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    # A fresh topic was created exactly once (id 1000 from _FakeApi).
    assert len(fake.create_calls) == 1

    # At least one send succeeded to the NEW topic id, not the stale 999.
    new_topic_id = 1000
    successful = [c for c in fake.send_calls if c[2] == new_topic_id]
    assert successful
    assert all(c[2] != 999 for c in fake.send_calls)

    # In-memory thread metadata now points at the new topic; stale 999 is cleared.
    assert thread.metadata["telegram_topic_id"] == new_topic_id

    @database_sync_to_async
    def _stored_topic():
        return Thread.objects.get(id=thread.id).metadata.get("telegram_topic_id")

    assert await _stored_topic() == new_topic_id
