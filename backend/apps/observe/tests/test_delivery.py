import pytest
from channels.db import database_sync_to_async

from apps.observe.delivery import TELEGRAM_MAX, _topic_name, deliver_turn, pick_color
from apps.observe.service import apply_session_meta, get_or_create_observed_thread
from apps.telegram.telegram_api import FORUM_ICON_COLORS
from apps.threads.models import Thread


class _FakeApi:
    def __init__(self):
        self._next_id = 1000
        self.create_calls = []
        self.send_calls = []
        self.send_kwargs = []

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
    ):
        self.send_calls.append((chat_id, text, message_thread_id, parse_mode))
        self.send_kwargs.append(
            {
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_creates_one_topic_per_session_and_routes():
    """GIVEN two sessions WHEN turns delivered THEN one topic per session, routed."""
    fake = _FakeApi()
    thread_a = await database_sync_to_async(get_or_create_observed_thread)(
        "Saaaaaaa", "/tmp/a.jsonl"
    )
    thread_b = await database_sync_to_async(get_or_create_observed_thread)(
        "Sbbbbbbb", "/tmp/b.jsonl"
    )

    turn_a1 = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Saaaaaaa"}
    turn_a2 = {"role": "assistant", "text": "yo", "uuid": "2", "session_id": "Saaaaaaa"}
    turn_b1 = {"role": "user", "text": "hey", "uuid": "3", "session_id": "Sbbbbbbb"}

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
        assert parse_mode == "HTML"
        assert text.startswith("<b>")

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
    thread = await database_sync_to_async(get_or_create_observed_thread)(
        "Slooooong", "/tmp/l.jsonl"
    )
    turn = {
        "role": "assistant",
        "text": "x" * (TELEGRAM_MAX + 500),
        "uuid": "1",
        "session_id": "Slooooong",
    }

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

    fake = _FailingHtmlApi()
    thread = await database_sync_to_async(get_or_create_observed_thread)(
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
    thread = await database_sync_to_async(get_or_create_observed_thread)(
        "Smeta123", "/tmp/m.jsonl"
    )
    await database_sync_to_async(apply_session_meta)(
        thread,
        {"repo": "agent-command-center", "branch": "claude/x", "title": "My Title"},
    )

    assert _topic_name(thread) == "claude_code · agent-command-center · My Title"
    assert len(_topic_name(thread)) <= 128

    turn1 = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Smeta123"}
    turn2 = {"role": "assistant", "text": "yo", "uuid": "2", "session_id": "Smeta123"}

    await deliver_turn(thread, turn1, None, forum_chat_id=-100999, api=fake)
    await deliver_turn(thread, turn2, None, forum_chat_id=-100999, api=fake)

    assert len(fake.create_calls) == 1
    _chat_id, name, _color = fake.create_calls[0]
    assert name == "claude_code · agent-command-center · My Title"

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
async def test_turn_is_silent_while_intro_notifies():
    """
    GIVEN a new observed session
    WHEN the first turn is delivered
    THEN the topic-intro message notifies and the turn message is silent.
    """
    fake = _FakeApi()
    thread = await database_sync_to_async(get_or_create_observed_thread)(
        "Ssilent1", "/tmp/silent.jsonl"
    )
    turn = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Ssilent1"}

    await deliver_turn(thread, turn, None, forum_chat_id=-100999, api=fake)

    # First send is the intro (default notification); the turn send is silent.
    assert fake.send_kwargs[0]["disable_notification"] is None
    assert fake.send_kwargs[-1]["disable_notification"] is True


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
    thread = await database_sync_to_async(get_or_create_observed_thread)(
        "Sforum01", "/tmp/fc.jsonl"
    )
    turn = {"role": "user", "text": "hi", "uuid": "1", "session_id": "Sforum01"}

    await deliver_turn(thread, turn, None, forum_chat_id=-100777, api=fake)

    @database_sync_to_async
    def _meta():
        t = Thread.objects.get(id=thread.id)
        return t.metadata.get("telegram_forum_chat_id"), t.metadata.get("telegram_topic_id")

    forum_id, topic_id = await _meta()
    assert topic_id is not None
    assert forum_id == -100777
