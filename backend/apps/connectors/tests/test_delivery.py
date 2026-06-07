"""
Delivery routing tests for apps.connectors.service.

Telegram is monkeypatched — no network calls.
Gateway enqueue is tested separately in apps/gateway/tests/.
"""

import pytest

from apps.connectors import service as connector_service

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_async_spy():
    """Return (spy_list, async coroutine) that records every call."""
    calls = []

    async def _spy(*args, **kwargs):
        calls.append((args, kwargs))

    return calls, _spy


# ---------------------------------------------------------------------------
# start_session() delivery routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStartSessionDelivery:
    def test_announces_to_telegram_when_chat_id_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_PROMPT_CHAT_ID is set and telegram send_message is patched
        WHEN service.start_session() is called
        THEN the session-started announcement is sent once to that chat
        """
        settings.ORC_PROMPT_CHAT_ID = "777"

        calls, spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", spy)

        result = connector_service.start_session(
            connector_id="s-tg-1",
            tool="claude_code",
            workspace_root="/tmp",
            name="Hotfix",
        )

        assert result["name"] == "Hotfix"
        assert len(calls) == 1
        chat_id, text = calls[0][0]
        assert chat_id == 777
        assert "Hotfix" in text

    def test_no_announcement_when_no_surface_configured(self, settings, monkeypatch):
        """
        GIVEN no surface is configured
        WHEN service.start_session() is called
        THEN no delivery happens and the session is still created
        """
        settings.ORC_PROMPT_CHAT_ID = ""

        tg_calls, tg_spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)

        result = connector_service.start_session(
            connector_id="s-none-1",
            tool="claude_code",
            workspace_root="/tmp",
            name="Quiet",
        )

        assert result["name"] == "Quiet"
        assert len(tg_calls) == 0


# ---------------------------------------------------------------------------
# ask() delivery routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAskDelivery:
    def test_telegram_surface_called_when_chat_id_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_PROMPT_CHAT_ID is set and telegram send_message is patched
        WHEN service.ask() is called
        THEN send_message is invoked once
        """
        settings.ORC_PROMPT_CHAT_ID = "999"

        calls, spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", spy)

        nonce = connector_service.ask(
            connector_id="d-tg-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Deploy?",
            options=["yes", "no"],
        )

        assert nonce
        assert len(calls) == 1
        # first positional arg is chat_id (as int)
        assert calls[0][0][0] == 999

    def test_no_surface_called_when_none_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_PROMPT_CHAT_ID is not set
        WHEN service.ask() is called
        THEN no delivery function is called and no error is raised
        """
        settings.ORC_PROMPT_CHAT_ID = ""

        tg_calls, tg_spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)

        nonce = connector_service.ask(
            connector_id="d-none-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Silent?",
            options=[],
        )

        assert nonce
        assert len(tg_calls) == 0
