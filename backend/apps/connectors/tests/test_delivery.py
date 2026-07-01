"""
Delivery routing tests for apps.connectors.service.

Single-platform model: the operator picks ONE messaging app and every
prompt/notification goes there only. Telegram send is monkeypatched — no
network calls. GatewayMessage rows are the observable side-effect for
non-Telegram platforms.
"""

import pytest

from apps.connectors import service as connector_service
from apps.threads.models import Thread

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
# start_session() → tail.start dispatch to the daemon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStartSessionTailStartDispatch:
    def test_sends_tail_start_with_claude_session_id_and_cwd(self, monkeypatch):
        """
        GIVEN exactly one enrolled host (unambiguous auto-bind)
        WHEN start_session runs
        THEN send_host_command is called once with command="tail.start" and the
             new thread's claude_session_id + cwd.
        """
        from apps.hosts.models import Host

        host = Host.objects.create(slug="ts-host", name="ts-host", os="linux")

        calls = []

        def fake_send_host_command(host_arg, command, **payload):
            calls.append((host_arg, command, payload))

        monkeypatch.setattr(
            "apps.hostlink.service.send_host_command", fake_send_host_command
        )

        result = connector_service.start_session(
            connector_id="ts-conn-1",
            tool="claude",
            workspace_root="/tmp/my-repo",
            name="Tail dispatch test",
        )

        thread = Thread.objects.get(id=result["thread_id"])
        assert len(calls) == 1
        called_host, command, payload = calls[0]
        assert called_host.id == host.id
        assert command == "tail.start"
        assert payload["thread_id"] == str(thread.id)
        assert payload["claude_session_id"] == thread.metadata["claude_session_id"]
        assert payload["cwd"] == "/tmp/my-repo"
        assert payload["provider"] == "claude"

    def test_no_host_enrolled_skips_dispatch(self, monkeypatch):
        """
        GIVEN no enrolled host
        WHEN start_session runs
        THEN it falls back to a read-only API thread and no tail.start is sent.
        """
        calls = []

        def fake_send_host_command(host_arg, command, **payload):
            calls.append((host_arg, command, payload))

        monkeypatch.setattr(
            "apps.hostlink.service.send_host_command", fake_send_host_command
        )

        result = connector_service.start_session(
            connector_id="ts-conn-2",
            tool="claude",
            workspace_root="/tmp/other-repo",
            name="No host test",
        )

        thread = Thread.objects.get(id=result["thread_id"])
        assert thread.runtime_mode == Thread.RuntimeModeChoices.API
        assert calls == []


# ---------------------------------------------------------------------------
# start_session() delivery routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStartSessionDelivery:
    def test_announces_to_telegram_when_chat_id_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=telegram and ORC_PROMPT_CHAT_ID is set
        WHEN service.start_session() is called
        THEN the announcement is sent once via telegram send_message; no GatewayMessage rows created
        """
        settings.ORC_MESSAGING_PLATFORM = "telegram"
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

        from apps.gateway.models import GatewayMessage
        assert GatewayMessage.objects.count() == 0

    def test_no_announcement_when_no_surface_configured(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=telegram and ORC_PROMPT_CHAT_ID is empty
        WHEN service.start_session() is called
        THEN no delivery happens and the session is still created
        """
        settings.ORC_MESSAGING_PLATFORM = "telegram"
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
# ask() / approve() delivery routing — Telegram
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAskDeliveryTelegram:
    def test_telegram_send_called_when_active(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=telegram and ORC_PROMPT_CHAT_ID is set
        WHEN service.ask() is called
        THEN send_message is invoked once with the correct chat_id; no GatewayMessage rows created
        """
        settings.ORC_MESSAGING_PLATFORM = "telegram"
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
        assert calls[0][0][0] == 999

        from apps.gateway.models import GatewayMessage
        assert GatewayMessage.objects.count() == 0

    def test_approve_calls_telegram_send(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=telegram and ORC_PROMPT_CHAT_ID is set
        WHEN service.approve() is called
        THEN send_message is invoked once; no GatewayMessage rows created
        """
        settings.ORC_MESSAGING_PLATFORM = "telegram"
        settings.ORC_PROMPT_CHAT_ID = "111"

        calls, spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", spy)

        nonce = connector_service.approve(
            connector_id="d-tg-2",
            tool="claude_code",
            workspace_root="/tmp",
            action="rm -rf /tmp/test",
            preview="This will delete the test directory.",
        )

        assert nonce
        assert len(calls) == 1

        from apps.gateway.models import GatewayMessage
        assert GatewayMessage.objects.count() == 0


# ---------------------------------------------------------------------------
# ask() delivery routing — WhatsApp (gateway platform)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAskDeliveryWhatsApp:
    def test_ask_enqueues_exactly_one_gateway_message_for_whatsapp(
        self, settings, monkeypatch
    ):
        """
        GIVEN ORC_MESSAGING_PLATFORM=whatsapp and ORC_PROMPT_WHATSAPP is set
              (with ORC_PROMPT_SLACK also set — must NOT enqueue to slack)
        WHEN service.ask() is called
        THEN exactly one GatewayMessage with platform=whatsapp is created;
             telegram send_message is NOT called
        """
        settings.ORC_MESSAGING_PLATFORM = "whatsapp"
        settings.ORC_PROMPT_WHATSAPP = "+41791234567"
        settings.ORC_PROMPT_SLACK = "#general"  # must be ignored
        settings.ORC_PROMPT_CHAT_ID = ""

        tg_calls, tg_spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)

        nonce = connector_service.ask(
            connector_id="d-wa-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Deploy to prod?",
            options=["yes", "no"],
        )

        assert nonce
        assert len(tg_calls) == 0

        from apps.gateway.models import GatewayMessage
        msgs = list(GatewayMessage.objects.all())
        assert len(msgs) == 1
        assert msgs[0].platform == "whatsapp"
        assert msgs[0].recipient == "+41791234567"
        assert msgs[0].prompt_nonce == nonce


# ---------------------------------------------------------------------------
# notify() delivery routing — WhatsApp
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNotifyDeliveryWhatsApp:
    def test_notify_enqueues_gateway_message_for_whatsapp(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=whatsapp and ORC_PROMPT_WHATSAPP is set
        WHEN service.notify() is called
        THEN exactly one GatewayMessage with platform=whatsapp is created;
             telegram send_message is NOT called
        """
        settings.ORC_MESSAGING_PLATFORM = "whatsapp"
        settings.ORC_PROMPT_WHATSAPP = "+41797654321"
        settings.ORC_PROMPT_CHAT_ID = ""

        tg_calls, tg_spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)

        connector_service.notify(
            connector_id="n-wa-1",
            tool="claude_code",
            workspace_root="/tmp",
            message="Build complete",
        )

        assert len(tg_calls) == 0

        from apps.gateway.models import GatewayMessage
        msgs = list(GatewayMessage.objects.all())
        assert len(msgs) == 1
        assert msgs[0].platform == "whatsapp"
        assert msgs[0].recipient == "+41797654321"
        assert "Build complete" in msgs[0].text


# ---------------------------------------------------------------------------
# Unconfigured recipient — no send, no exception
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnconfiguredRecipient:
    def test_ask_no_send_when_recipient_empty(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=telegram but ORC_PROMPT_CHAT_ID is empty
        WHEN service.ask() is called
        THEN no send_message call is made and no exception is raised
        """
        settings.ORC_MESSAGING_PLATFORM = "telegram"
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

        from apps.gateway.models import GatewayMessage
        assert GatewayMessage.objects.count() == 0

    def test_ask_no_send_when_whatsapp_recipient_empty(self, settings, monkeypatch):
        """
        GIVEN ORC_MESSAGING_PLATFORM=whatsapp but ORC_PROMPT_WHATSAPP is empty
        WHEN service.ask() is called
        THEN no GatewayMessage is created and no exception is raised
        """
        settings.ORC_MESSAGING_PLATFORM = "whatsapp"
        settings.ORC_PROMPT_WHATSAPP = ""
        settings.ORC_PROMPT_CHAT_ID = ""

        tg_calls, tg_spy = _make_async_spy()
        import apps.telegram.telegram_api as tg_api
        monkeypatch.setattr(tg_api, "send_message", tg_spy)

        nonce = connector_service.ask(
            connector_id="d-wa-none-1",
            tool="claude_code",
            workspace_root="/tmp",
            question="Silent via WhatsApp?",
            options=[],
        )

        assert nonce
        assert len(tg_calls) == 0

        from apps.gateway.models import GatewayMessage
        assert GatewayMessage.objects.count() == 0
