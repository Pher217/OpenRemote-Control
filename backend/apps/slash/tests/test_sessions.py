"""Tests for Fleet View F1 — /sessions handler and render_fleet.

Coverage:
- render_fleet: grouping, badges, age/idle formatting, empty fleet, topic link.
- handle: auth gate (non-allowlisted → drop; allowlisted → ok).
- Fleet dashboard: first call sends+pins; second call edits; edit failure falls back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

from apps.accounts.models import Account
from apps.slash.handlers.sessions import (
    _needs_input,
    _topic_link,
    handle,
    render_fleet,
)
from apps.threads.models import Thread

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_account(suffix: str = "") -> Account:
    return Account.objects.create(
        provider="anthropic",
        label=f"test-sess{suffix}",
        auth_type="none",
        credential_type="none",
    )


def _make_thread(
    account: Account,
    *,
    runtime_mode: str = Thread.RuntimeModeChoices.OBSERVED,
    status: str = Thread.StatusChoices.RUNNING,
    name: str = "test-thread",
    started_at: datetime | None = None,
    last_event_at: datetime | None = None,
    metadata: dict | None = None,
) -> Thread:
    now = datetime.now(tz=UTC)
    return Thread.objects.create(
        name=name,
        runtime="claude_code",
        runtime_mode=runtime_mode,
        status=status,
        account=account,
        started_at=started_at or (now - timedelta(minutes=5)),
        last_event_at=last_event_at or (now - timedelta(minutes=1)),
        metadata=metadata or {},
    )


@pytest.fixture
def account(db):
    return _make_account()


# ---------------------------------------------------------------------------
# render_fleet — pure unit tests (no DB)
# ---------------------------------------------------------------------------


def _fake_thread(
    *,
    runtime_mode: str = Thread.RuntimeModeChoices.OBSERVED,
    status: str = Thread.StatusChoices.RUNNING,
    name: str = "proj",
    host_name: str | None = None,
    started_at: datetime | None = None,
    last_event_at: datetime | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like a Thread for render_fleet."""
    now = datetime.now(tz=UTC)
    t = MagicMock(spec=Thread)
    t.runtime_mode = runtime_mode
    t.status = status
    t.name = name
    t.project = MagicMock()
    t.project.name = name
    t.started_at = started_at or (now - timedelta(minutes=10))
    t.last_event_at = last_event_at or (now - timedelta(minutes=2))
    t.metadata = metadata or {}
    if host_name:
        t.host = MagicMock()
        t.host.name = host_name
    else:
        t.host = None
    return t


class TestRenderFleetEmpty:
    def test_empty_fleet_returns_no_active_sessions(self):
        """
        GIVEN no threads
        WHEN render_fleet is called with an empty list
        THEN the result is "No active sessions."
        """
        result = render_fleet([], datetime.now(tz=UTC))
        assert result == "No active sessions."


class TestRenderFleetGrouping:
    def test_needs_input_group_appears_first(self):
        """
        GIVEN threads in waiting_approval, running, and pending status
        WHEN render_fleet is called
        THEN the Needs input group appears before Working.
        """
        now = datetime.now(tz=UTC)
        waiting = _fake_thread(status=Thread.StatusChoices.WAITING_APPROVAL, name="waiting")
        running = _fake_thread(status=Thread.StatusChoices.RUNNING, name="running")

        result = render_fleet([waiting, running], now)

        needs_pos = result.index("Needs input")
        working_pos = result.index("Working")
        assert needs_pos < working_pos

    def test_waiting_approval_goes_to_needs_input_group(self):
        """
        GIVEN a thread with status waiting_approval
        WHEN render_fleet is called
        THEN the thread appears under Needs input.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.WAITING_APPROVAL, name="my-project")
        result = render_fleet([t], now)

        assert "Needs input" in result
        needs_section = result[result.index("Needs input"):]
        assert "my-project" in needs_section

    def test_running_thread_goes_to_working_group(self):
        """
        GIVEN a thread with status running
        WHEN render_fleet is called
        THEN the thread appears under Working.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.RUNNING, name="work-proj")
        result = render_fleet([t], now)

        assert "Working" in result
        working_section = result[result.index("Working"):]
        assert "work-proj" in working_section

    def test_other_active_status_goes_to_idle_other_group(self):
        """
        GIVEN a thread with a status that is not waiting_approval / running / starting / pending
        WHEN render_fleet is called
        THEN the thread appears under Idle / other.
        """
        now = datetime.now(tz=UTC)
        # Use a mock status that doesn't match any group rules
        t = _fake_thread(status="other_active", name="idle-proj")
        result = render_fleet([t], now)

        assert "Idle / other" in result
        idle_section = result[result.index("Idle / other"):]
        assert "idle-proj" in idle_section


class TestRenderFleetBadges:
    @pytest.mark.parametrize(
        "runtime_mode,expected_badge",
        [
            (Thread.RuntimeModeChoices.PTY, "Claude (PTY)"),
            (Thread.RuntimeModeChoices.RC, "Claude (RC)"),
            (Thread.RuntimeModeChoices.EXEC, "Claude (exec)"),
            (Thread.RuntimeModeChoices.API, "Claude (API)"),
            (Thread.RuntimeModeChoices.SDK, "Claude (SDK)"),
            (Thread.RuntimeModeChoices.OBSERVED, "Claude (observed)"),
            (Thread.RuntimeModeChoices.OPENCLAW, "OpenClaw"),
            (Thread.RuntimeModeChoices.HERMES, "Hermes"),
        ],
    )
    def test_badge_per_runtime_mode(self, runtime_mode, expected_badge):
        """
        GIVEN a thread with a specific runtime_mode
        WHEN render_fleet is called
        THEN the rendered line contains the correct badge text.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(runtime_mode=runtime_mode, status=Thread.StatusChoices.RUNNING)
        result = render_fleet([t], now)
        assert expected_badge in result


class TestRenderFleetAgeIdle:
    def test_age_shown_as_minutes(self):
        """
        GIVEN a thread started 30 minutes ago
        WHEN render_fleet is called
        THEN the age shows '30m'.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(
            status=Thread.StatusChoices.RUNNING,
            started_at=now - timedelta(minutes=30),
            last_event_at=now - timedelta(minutes=5),
        )
        result = render_fleet([t], now)
        assert "age 30m" in result

    def test_age_shown_as_hours_and_minutes(self):
        """
        GIVEN a thread started 2h 15m ago
        WHEN render_fleet is called
        THEN the age shows '2h 15m'.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(
            status=Thread.StatusChoices.RUNNING,
            started_at=now - timedelta(hours=2, minutes=15),
            last_event_at=now - timedelta(minutes=1),
        )
        result = render_fleet([t], now)
        assert "age 2h 15m" in result

    def test_idle_shown_separately_from_age(self):
        """
        GIVEN a thread started 45m ago with last event 3m ago
        WHEN render_fleet is called
        THEN age is 45m and idle is 3m (both shown independently).
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(
            status=Thread.StatusChoices.RUNNING,
            started_at=now - timedelta(minutes=45),
            last_event_at=now - timedelta(minutes=3),
        )
        result = render_fleet([t], now)
        assert "age 45m" in result
        assert "idle 3m" in result

    def test_none_started_at_shows_question_mark(self):
        """
        GIVEN a thread with no started_at
        WHEN render_fleet is called
        THEN age shows '?'.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.RUNNING, started_at=now)
        t.started_at = None
        result = render_fleet([t], now)
        assert "age ?" in result


class TestRenderFleetTopicLink:
    def test_topic_deep_link_present_when_topic_id_set(self):
        """
        GIVEN a thread with telegram_topic_id and telegram_forum_chat_id in metadata
        WHEN render_fleet is called
        THEN the rendered line contains an HTML anchor with the deep-link URL.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(
            status=Thread.StatusChoices.RUNNING,
            metadata={"telegram_topic_id": 42, "telegram_forum_chat_id": -1001234567890},
        )
        result = render_fleet([t], now)
        assert "https://t.me/c/" in result
        assert "/42" in result

    def test_no_link_when_topic_id_missing(self):
        """
        GIVEN a thread with no telegram_topic_id
        WHEN render_fleet is called
        THEN no anchor tag is present for that thread.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.RUNNING, metadata={})
        result = render_fleet([t], now)
        assert "https://t.me/c/" not in result

    def test_host_name_shown_in_line(self):
        """
        GIVEN a thread with a host
        WHEN render_fleet is called
        THEN the host name appears in the line.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.RUNNING, host_name="my-mac")
        result = render_fleet([t], now)
        assert "my-mac" in result

    def test_local_shown_when_no_host(self):
        """
        GIVEN a thread with no host
        WHEN render_fleet is called
        THEN 'local' appears in the line.
        """
        now = datetime.now(tz=UTC)
        t = _fake_thread(status=Thread.StatusChoices.RUNNING)  # host=None
        result = render_fleet([t], now)
        assert "local" in result


class TestTopicLink:
    def test_topic_link_strips_minus_100_prefix(self):
        """
        GIVEN forum_chat_id=-1001234567890 and topic_id=42
        WHEN _topic_link is called
        THEN the URL uses 1234567890 (stripped -100 prefix).
        """
        t = MagicMock(spec=Thread)
        t.metadata = {"telegram_topic_id": 42, "telegram_forum_chat_id": -1001234567890}
        link = _topic_link(t)
        assert link == "https://t.me/c/1234567890/42"

    def test_topic_link_none_when_no_topic_id(self):
        """
        GIVEN metadata with no telegram_topic_id
        WHEN _topic_link is called
        THEN None is returned.
        """
        t = MagicMock(spec=Thread)
        t.metadata = {}
        assert _topic_link(t) is None


# ---------------------------------------------------------------------------
# handle — auth gate (DB required for settings fixture)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_handle_non_allowlisted_user_drops(settings):
    """
    GIVEN a from_user_id that is NOT in TELEGRAM_ALLOWED_CHAT_IDS
    WHEN handle is called
    THEN result has drop=True and ok=False (no Telegram send triggered).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}

    fake_thread = MagicMock(spec=Thread)
    result = handle(fake_thread, [], from_user_id=999)

    assert result["ok"] is False
    assert result.get("drop") is True


@pytest.mark.django_db
def test_handle_allowlisted_user_returns_ok(settings, account):
    """
    GIVEN a from_user_id that IS in TELEGRAM_ALLOWED_CHAT_IDS
    WHEN handle is called
    THEN result has ok=True and text is a non-empty string.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}

    fake_thread = MagicMock(spec=Thread)
    result = handle(fake_thread, [], from_user_id=111)

    assert result["ok"] is True
    assert isinstance(result["text"], str)
    assert len(result["text"]) > 0


@pytest.mark.django_db
def test_handle_no_from_user_id_skips_auth_check(account):
    """
    GIVEN handle is called with no from_user_id
    WHEN handle is called
    THEN auth check is skipped and result has ok=True.
    """
    fake_thread = MagicMock(spec=Thread)
    result = handle(fake_thread, [])
    assert result["ok"] is True


@pytest.mark.django_db
def test_handle_returns_refresh_dashboard_flag(settings, account):
    """
    GIVEN an allowlisted user
    WHEN handle is called
    THEN result contains refresh_dashboard=True.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    fake_thread = MagicMock(spec=Thread)
    result = handle(fake_thread, [], from_user_id=111)
    assert result.get("refresh_dashboard") is True


# ---------------------------------------------------------------------------
# needs_input heuristic
# ---------------------------------------------------------------------------


def test_needs_input_true_for_waiting_approval():
    """
    GIVEN a thread with status waiting_approval
    WHEN _needs_input is called
    THEN it returns True.
    """
    t = MagicMock(spec=Thread)
    t.status = Thread.StatusChoices.WAITING_APPROVAL
    assert _needs_input(t) is True


def test_needs_input_false_for_running():
    """
    GIVEN a thread with status running
    WHEN _needs_input is called
    THEN it returns False.
    """
    t = MagicMock(spec=Thread)
    t.status = Thread.StatusChoices.RUNNING
    assert _needs_input(t) is False


# ---------------------------------------------------------------------------
# Fleet dashboard tests (async)
# ---------------------------------------------------------------------------


class _FakeDashApi:
    """Minimal Bot API stub for dashboard tests."""

    def __init__(self):
        self._next_id = 200
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.pin_calls: list[tuple] = []
        self._edit_ok = True

    async def send_message(
        self,
        chat_id,
        text,
        message_thread_id=None,
        parse_mode=None,
        disable_notification=None,
    ) -> int:
        msg_id = self._next_id
        self._next_id += 1
        self.send_calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "message_thread_id": message_thread_id,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
        )
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
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
            }
        )
        return self._edit_ok

    async def pin_chat_message(self, chat_id, message_id, *, disable_notification=True) -> bool:
        self.pin_calls.append((chat_id, message_id))
        return True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dashboard_first_call_sends_and_pins():
    """
    GIVEN no cached dashboard message id
    WHEN refresh_fleet_dashboard is called for the first time
    THEN a message is sent with disable_notification=True and pinned.
    """
    from django.core.cache import cache

    from apps.slash.fleet_dashboard import DASHBOARD_CACHE_KEY, refresh_fleet_dashboard

    cache.delete(DASHBOARD_CACHE_KEY)
    api = _FakeDashApi()

    await refresh_fleet_dashboard(forum_chat_id=-100999, api=api)

    assert len(api.send_calls) == 1
    assert api.send_calls[0]["disable_notification"] is True
    assert api.send_calls[0]["parse_mode"] == "HTML"
    assert len(api.pin_calls) == 1
    assert api.pin_calls[0] == (-100999, 200)  # first msg_id from stub

    stored = cache.get(DASHBOARD_CACHE_KEY)
    assert stored == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dashboard_second_call_edits_stored_id():
    """
    GIVEN a cached dashboard message id
    WHEN refresh_fleet_dashboard is called again
    THEN edit_message_text is called with the stored id (no new send, no re-pin).
    """
    from django.core.cache import cache

    from apps.slash.fleet_dashboard import DASHBOARD_CACHE_KEY, refresh_fleet_dashboard

    cache.set(DASHBOARD_CACHE_KEY, 555, 3600)
    api = _FakeDashApi()

    await refresh_fleet_dashboard(forum_chat_id=-100999, api=api)

    assert len(api.send_calls) == 0
    assert len(api.edit_calls) == 1
    assert api.edit_calls[0]["message_id"] == 555
    assert len(api.pin_calls) == 0

    # Stored id is unchanged.
    assert cache.get(DASHBOARD_CACHE_KEY) == 555


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dashboard_edit_failure_falls_back_to_fresh_send_and_repin():
    """
    GIVEN a cached dashboard message id whose edit fails (message deleted)
    WHEN refresh_fleet_dashboard is called
    THEN a fresh message is sent and pinned, and the cache id is updated.
    """
    from django.core.cache import cache

    from apps.slash.fleet_dashboard import DASHBOARD_CACHE_KEY, refresh_fleet_dashboard

    cache.set(DASHBOARD_CACHE_KEY, 555, 3600)
    api = _FakeDashApi()
    api._edit_ok = False  # simulate edit failure

    await refresh_fleet_dashboard(forum_chat_id=-100999, api=api)

    # One edit attempt (failed), then one fresh send.
    assert len(api.edit_calls) == 1
    assert api.edit_calls[0]["message_id"] == 555
    assert len(api.send_calls) == 1
    # Cache updated to the new message id.
    new_stored = cache.get(DASHBOARD_CACHE_KEY)
    assert new_stored is not None
    assert new_stored != 555
    # Re-pinned with the new id.
    assert len(api.pin_calls) == 1
    assert api.pin_calls[0] == (-100999, new_stored)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dashboard_no_op_when_forum_chat_id_not_configured():
    """
    GIVEN TELEGRAM_FORUM_CHAT_ID is empty and forum_chat_id not passed
    WHEN refresh_fleet_dashboard is called
    THEN nothing is sent (early return).
    """
    from django.core.cache import cache

    from apps.slash.fleet_dashboard import DASHBOARD_CACHE_KEY, refresh_fleet_dashboard

    cache.delete(DASHBOARD_CACHE_KEY)
    api = _FakeDashApi()

    with override_settings(TELEGRAM_FORUM_CHAT_ID=""):
        await refresh_fleet_dashboard(api=api)

    assert len(api.send_calls) == 0
    assert len(api.edit_calls) == 0
