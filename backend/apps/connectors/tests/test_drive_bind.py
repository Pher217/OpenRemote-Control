"""start_session binds the driveable thread to the caller's own session.

When `/openremote-control` passes the calling Claude Code session id
(CLAUDE_CODE_SESSION_ID), the dispatched chat must drive THAT session — a
Telegram reply runs `claude -p --resume <id>` and continues this conversation,
rather than spinning up a fresh session. Without an id, a new session is minted.
"""

from unittest.mock import patch

import pytest

from apps.connectors.service import start_session
from apps.hosts.models import Host
from apps.threads.models import Thread


@pytest.fixture
def telegram_forum(settings):
    settings.ORC_MESSAGING_PLATFORM = "telegram"
    settings.ORC_PROMPT_CHAT_ID = "-100999"
    settings.TELEGRAM_FORUM_CHAT_ID = "-100999"
    return -100999


@pytest.fixture
def host(db):
    return Host.objects.create(slug="mac", name="mac", os="darwin")


def _patched_start(*args, **kwargs):
    async def fake_create_topic(chat_id, name, color):
        return 4242

    async def fake_send(chat_id, text, message_thread_id=None, **kw):
        return None

    with (
        patch("apps.telegram.telegram_api.create_forum_topic", fake_create_topic),
        patch("apps.telegram.telegram_api.send_message", fake_send),
    ):
        return start_session(*args, **kwargs)


@pytest.mark.django_db
def test_start_session_binds_to_provided_claude_session_id(telegram_forum, host):
    """
    GIVEN one enrolled host AND a caller-supplied claude_session_id
    WHEN  start_session runs
    THEN  the driveable thread is bound to THAT id and marked already-started
          (so the first Telegram reply resumes, not creates).
    """
    out = _patched_start(
        "conn-bind",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="e2b2c396-507b-4e1a-bc81-c23294821676",
    )

    thread = Thread.objects.get(id=out["thread_id"])
    assert thread.metadata["headless"] is True
    assert thread.metadata["claude_session_id"] == "e2b2c396-507b-4e1a-bc81-c23294821676"
    assert thread.metadata["claude_session_started"] is True
    assert thread.metadata["cwd"] == "/Users/me/dev/proj"


@pytest.mark.django_db
def test_start_session_mints_fresh_id_when_unbound(telegram_forum, host):
    """
    GIVEN one enrolled host AND no caller session id
    WHEN  start_session runs
    THEN  a fresh session id is minted and NOT marked started (first reply creates).
    """
    out = _patched_start("conn-fresh", "claude_code", "/tmp/ws", "Fresh")

    thread = Thread.objects.get(id=out["thread_id"])
    assert thread.metadata["headless"] is True
    assert thread.metadata["claude_session_id"]  # some uuid
    assert thread.metadata["claude_session_id"] != ""
    assert thread.metadata["claude_session_started"] is False
