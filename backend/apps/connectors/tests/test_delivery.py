"""
Delivery routing tests for apps.connectors.service.

Both surfaces (Telegram and Matrix) are monkeypatched — no network calls.
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
# ask() delivery routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAskDelivery:
    def test_matrix_surface_called_when_room_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_PROMPT_MATRIX_ROOM is set and Matrix send_text is patched
        WHEN service.ask() is called
        THEN send_text is invoked with the configured room and rendered prompt text
        """
        settings.ORC_PROMPT_MATRIX_ROOM = "!testroom:example.org"
        settings.ORC_PROMPT_CHAT_ID = ""

        calls, spy = _make_async_spy()
        import apps.matrix.client as matrix_client
        monkeypatch.setattr(matrix_client, "send_text", spy)

        nonce = connector_service.ask(
            connector_id="d-matrix-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Pick a branch?",
            options=["main", "develop"],
        )

        assert nonce
        assert len(calls) == 1
        room_id, text = calls[0][0]
        assert room_id == "!testroom:example.org"
        # render_prompt produces a numbered list
        assert "Pick a branch?" in text
        assert "1." in text
        assert "main" in text

    def test_telegram_surface_called_when_chat_id_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_PROMPT_CHAT_ID is set and telegram send_message is patched
        WHEN service.ask() is called
        THEN send_message is invoked once
        """
        settings.ORC_PROMPT_CHAT_ID = "999"
        settings.ORC_PROMPT_MATRIX_ROOM = ""

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

    def test_both_surfaces_called_when_both_configured(self, settings, monkeypatch):
        """
        GIVEN both ORC_PROMPT_CHAT_ID and ORC_PROMPT_MATRIX_ROOM are set
        WHEN service.ask() is called
        THEN both send_message and send_text are each called once
        """
        settings.ORC_PROMPT_CHAT_ID = "999"
        settings.ORC_PROMPT_MATRIX_ROOM = "!testroom:example.org"

        tg_calls, tg_spy = _make_async_spy()
        mx_calls, mx_spy = _make_async_spy()

        import apps.matrix.client as matrix_client
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)
        monkeypatch.setattr(matrix_client, "send_text", mx_spy)

        nonce = connector_service.ask(
            connector_id="d-both-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Which env?",
            options=["staging", "prod"],
        )

        assert nonce
        assert len(tg_calls) == 1
        assert len(mx_calls) == 1

    def test_neither_surface_called_when_none_configured(self, settings, monkeypatch):
        """
        GIVEN neither ORC_PROMPT_CHAT_ID nor ORC_PROMPT_MATRIX_ROOM is set
        WHEN service.ask() is called
        THEN no delivery function is called and no error is raised
        """
        settings.ORC_PROMPT_CHAT_ID = ""
        settings.ORC_PROMPT_MATRIX_ROOM = ""

        tg_calls, tg_spy = _make_async_spy()
        mx_calls, mx_spy = _make_async_spy()

        import apps.matrix.client as matrix_client
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)
        monkeypatch.setattr(matrix_client, "send_text", mx_spy)

        nonce = connector_service.ask(
            connector_id="d-none-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Silent?",
            options=[],
        )

        assert nonce
        assert len(tg_calls) == 0
        assert len(mx_calls) == 0
