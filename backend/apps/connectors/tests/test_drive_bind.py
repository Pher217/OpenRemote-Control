"""start_session binds the driveable thread to the caller's own session.

When `/openremote-control` passes the calling Claude Code session id
(CLAUDE_CODE_SESSION_ID), the dispatched chat must drive THAT session — a
Telegram reply runs `claude -p --resume <id>` and continues this conversation,
rather than spinning up a fresh session. Without an id, a new session is minted.
"""

from unittest.mock import patch

import pytest

from apps.connectors.service import _select_drive_host, start_session
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


# ---------------------------------------------------------------------------
# _select_drive_host — host resolution for driveable session binding
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSelectDriveHost:
    def test_single_host_always_selected(self):
        """
        GIVEN a single enrolled Host 'MacBook-Pro'
        WHEN  _select_drive_host is called with any hostname (including empty)
        THEN  that sole host is always returned.
        """
        only = Host.objects.create(slug="mbp", name="MacBook-Pro", os="darwin")

        assert _select_drive_host("anything").id == only.id
        assert _select_drive_host("").id == only.id

    def test_multi_host_exact_match(self):
        """
        GIVEN two enrolled hosts 'MacBook-Pro' and 'GamingPH'
        WHEN  _select_drive_host is called with a matching hostname
        THEN  it returns the exact-match host (case-insensitive, domain stripped).
        """
        mbp = Host.objects.create(slug="mbp", name="MacBook-Pro", os="darwin")
        gaming = Host.objects.create(slug="gph", name="GamingPH", os="win32")

        assert _select_drive_host("GamingPH").id == gaming.id
        assert _select_drive_host("MacBook-Pro.local").id == mbp.id

    def test_multi_host_no_hostname_returns_none(self):
        """
        GIVEN two enrolled hosts
        WHEN  _select_drive_host is called with an empty hostname
        THEN  None is returned (ambiguous, non-driveable).
        """
        Host.objects.create(slug="mbp", name="MacBook-Pro", os="darwin")
        Host.objects.create(slug="gph", name="GamingPH", os="win32")

        assert _select_drive_host("") is None

    def test_multi_host_unknown_hostname_returns_none(self):
        """
        GIVEN two enrolled hosts
        WHEN  _select_drive_host is called with a hostname matching no host
        THEN  None is returned (no match, non-driveable).
        """
        Host.objects.create(slug="mbp", name="MacBook-Pro", os="darwin")
        Host.objects.create(slug="gph", name="GamingPH", os="win32")

        assert _select_drive_host("SomeOtherBox") is None


@pytest.mark.django_db
def test_start_session_reuses_existing_thread_for_same_claude_session_id(telegram_forum, host):
    """
    GIVEN one enrolled host AND a caller-supplied claude_session_id
    WHEN  start_session is called twice with the SAME claude_session_id
    THEN  the returned thread_id is identical both times and only one Thread exists.
    """
    out1 = _patched_start(
        "conn-dup-a",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="e2b2c396-507b-4e1a-bc81-c23294821676",
    )
    out2 = _patched_start(
        "conn-dup-b",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="e2b2c396-507b-4e1a-bc81-c23294821676",
    )

    assert out1["thread_id"] == out2["thread_id"]
    assert Thread.objects.count() == 1


@pytest.mark.django_db
def test_start_session_creates_new_thread_for_different_claude_session_id(telegram_forum, host):
    """
    GIVEN one enrolled host AND two distinct caller-supplied claude_session_ids
    WHEN  start_session is called once with each id
    THEN  the two returned thread_ids differ and two Threads exist.
    """
    out1 = _patched_start(
        "conn-dup-c",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="11111111-1111-1111-1111-111111111111",
    )
    out2 = _patched_start(
        "conn-dup-d",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="22222222-2222-2222-2222-222222222222",
    )

    assert out1["thread_id"] != out2["thread_id"]
    assert Thread.objects.count() == 2



@pytest.mark.django_db
def test_start_session_marks_vscode_origin_as_non_driveable(telegram_forum, host):
    """
    GIVEN one enrolled host AND a caller-supplied claude_session_id
          AND entrypoint="claude-vscode" (VSCode-extension-hosted session)
    WHEN  start_session runs
    THEN  the created thread is marked non-driveable (headless is False) —
          such a session cannot be safely --resume'd without diverging from the
          live editor session.
    """
    out = _patched_start(
        "conn-vsc",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="e2b2c396-507b-4e1a-bc81-c23294821676",
        entrypoint="claude-vscode",
    )

    thread = Thread.objects.get(id=out["thread_id"])
    assert thread.metadata["headless"] is False


@pytest.mark.django_db
def test_start_session_defaults_to_driveable_without_vscode_entrypoint(telegram_forum, host):
    """
    GIVEN one enrolled host AND a caller-supplied claude_session_id
          with NO entrypoint (a normal CLI/headless session)
    WHEN  start_session runs
    THEN  the created thread remains driveable (headless is True) — unchanged.
    """
    out = _patched_start(
        "conn-novsc",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id="e2b2c396-507b-4e1a-bc81-c23294821676",
        entrypoint="",
    )

    thread = Thread.objects.get(id=out["thread_id"])
    assert thread.metadata["headless"] is True


@pytest.mark.django_db
def test_start_session_reuse_recomputes_headless_from_latest_entrypoint(telegram_forum, host):
    """
    GIVEN a thread already dispatched as driveable (no entrypoint)
    WHEN  start_session is called again for the SAME claude_session_id, this
          time with entrypoint="claude-vscode"
    THEN  the reused thread's headless flag flips to False — the reuse branch
          must recompute the drive gate, not trust stale metadata from creation.
    """
    session_id = "e2b2c396-507b-4e1a-bc81-c23294821676"
    out1 = _patched_start(
        "conn-reuse-a",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id=session_id,
        entrypoint="",
    )
    thread = Thread.objects.get(id=out1["thread_id"])
    assert thread.metadata["headless"] is True

    out2 = _patched_start(
        "conn-reuse-b",
        "claude_code",
        "/Users/me/dev/proj",
        "My session",
        claude_session_id=session_id,
        entrypoint="claude-vscode",
    )

    assert out2["thread_id"] == out1["thread_id"]
    thread.refresh_from_db()
    assert thread.metadata["headless"] is False


@pytest.mark.django_db(transaction=True)
def test_start_session_concurrent_dispatch_creates_only_one_topic(telegram_forum, host):
    """
    GIVEN one enrolled host AND a caller-supplied claude_session_id
    WHEN  two start_session() calls race for the SAME claude_session_id on
          separate threads/DB connections, with topic creation slowed down to
          widen the window between the Thread-reuse lock releasing and the
          topic-creation check running
    THEN  only ONE Telegram topic is created and both calls return the same
          thread_id — the topic-creation lock (not just the Thread-reuse lock)
          closes the race a design-then-implementation review pair missed.
    """
    import threading
    import time

    from django.db import connection

    session_id = "e2b2c396-507b-4e1a-bc81-c23294821676"
    create_calls = []
    create_lock = threading.Lock()

    async def slow_create_topic(chat_id, name, color):
        with create_lock:
            create_calls.append(1)
        time.sleep(0.2)
        return 4242

    async def fake_send(chat_id, text, message_thread_id=None, **kw):
        return None

    results = {}
    barrier = threading.Barrier(2)

    def _run(key):
        barrier.wait()
        try:
            results[key] = start_session(
                f"conn-race-{key}",
                "claude_code",
                "/Users/me/dev/proj",
                "My session",
                claude_session_id=session_id,
            )
        finally:
            connection.close()

    with (
        patch("apps.telegram.telegram_api.create_forum_topic", slow_create_topic),
        patch("apps.telegram.telegram_api.send_message", fake_send),
    ):
        t1 = threading.Thread(target=_run, args=("a",))
        t2 = threading.Thread(target=_run, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert len(create_calls) == 1
    assert results["a"]["thread_id"] == results["b"]["thread_id"]
    assert Thread.objects.filter(metadata__claude_session_id=session_id).count() == 1
