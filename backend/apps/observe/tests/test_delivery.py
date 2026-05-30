import pytest
from channels.db import database_sync_to_async

from apps.observe.delivery import TELEGRAM_MAX, deliver_turn, pick_color
from apps.observe.service import get_or_create_observed_thread
from apps.telegram.telegram_api import FORUM_ICON_COLORS
from apps.threads.models import Thread


class _FakeApi:
    def __init__(self):
        self._next_id = 1000
        self.create_calls = []
        self.send_calls = []

    async def create_forum_topic(self, chat_id, name, icon_color):
        self.create_calls.append((chat_id, name, icon_color))
        topic_id = self._next_id
        self._next_id += 1
        return topic_id

    async def send_message(self, chat_id, text, message_thread_id=None):
        self.send_calls.append((chat_id, text, message_thread_id))


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

    a_sends = [c for c in fake.send_calls if c[2] == topic_a]
    b_sends = [c for c in fake.send_calls if c[2] == topic_b]
    assert len(a_sends) == 2
    assert len(b_sends) == 1
    assert a_sends[0][1] == "user: hi"
    assert a_sends[1][1] == "assistant: yo"
    assert b_sends[0][1] == "user: hey"


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

    sent_text = fake.send_calls[0][1]
    assert len(sent_text) <= TELEGRAM_MAX
    assert sent_text.endswith("…")


def test_pick_color_deterministic():
    """GIVEN a session id WHEN picked twice THEN same valid palette color each time."""
    c1 = pick_color("Saaaaaaa")
    c2 = pick_color("Saaaaaaa")
    assert c1 == c2
    assert c1 in FORUM_ICON_COLORS
