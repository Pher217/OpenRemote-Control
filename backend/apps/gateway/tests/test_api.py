"""
Tests for the messaging gateway API endpoints.

External I/O (dispatch_text) is monkeypatched — no network calls.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Account
from apps.gateway.models import GatewayMessage
from apps.gateway.service import enqueue_text
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt
from apps.threads.models import Thread

TOKEN = "test-gateway-token-xyz"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
OUTBOX = "/api/gateway/outbox"
INBOUND = "/api/gateway/inbound"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def with_token(settings):
    settings.MESSAGING_GATEWAY_TOKEN = TOKEN
    return TOKEN


@pytest.fixture
def thread(db):
    account, _ = Account.objects.get_or_create(
        provider="whatsapp",
        label="gateway",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    return Thread.objects.create(
        name="gateway:whatsapp:+1234",
        runtime="whatsapp",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatewayAuth:
    def test_outbox_missing_token_returns_401(self, client, settings):
        """
        GIVEN MESSAGING_GATEWAY_TOKEN is set but no Authorization header is sent
        WHEN GET /api/gateway/outbox is called
        THEN 401 is returned
        """
        settings.MESSAGING_GATEWAY_TOKEN = TOKEN
        response = client.get(f"{OUTBOX}?platform=whatsapp")
        assert response.status_code == 401

    def test_outbox_wrong_token_returns_401(self, client, settings):
        """
        GIVEN MESSAGING_GATEWAY_TOKEN is set and a wrong token is provided
        WHEN GET /api/gateway/outbox is called
        THEN 401 is returned
        """
        settings.MESSAGING_GATEWAY_TOKEN = TOKEN
        response = client.get(
            f"{OUTBOX}?platform=whatsapp",
            **{"HTTP_AUTHORIZATION": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_outbox_unconfigured_token_returns_503(self, client, settings):
        """
        GIVEN MESSAGING_GATEWAY_TOKEN is empty
        WHEN GET /api/gateway/outbox is called (with any token)
        THEN 503 is returned
        """
        settings.MESSAGING_GATEWAY_TOKEN = ""
        response = client.get(f"{OUTBOX}?platform=whatsapp", **AUTH)
        assert response.status_code == 503

    def test_inbound_unconfigured_token_returns_503(self, client, settings):
        """
        GIVEN MESSAGING_GATEWAY_TOKEN is empty
        WHEN POST /api/gateway/inbound is called
        THEN 503 is returned
        """
        settings.MESSAGING_GATEWAY_TOKEN = ""
        response = client.post(
            INBOUND,
            {"platform": "whatsapp", "chat_id": "123", "sender": "user", "text": "hi"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Outbox tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOutbox:
    def test_enqueue_then_claim_returns_message_and_marks_delivered(self, client, with_token):
        """
        GIVEN a GatewayMessage is enqueued for whatsapp
        WHEN GET /api/gateway/outbox?platform=whatsapp
        THEN 200 {messages:[{id, platform, recipient, text}]} and delivered_at is set
        """
        enqueue_text("whatsapp", "+15551234", "Hello from gateway", "nonce123")

        response = client.get(f"{OUTBOX}?platform=whatsapp", **AUTH)
        assert response.status_code == 200
        messages = response.data["messages"]
        assert len(messages) == 1
        msg = messages[0]
        assert msg["platform"] == "whatsapp"
        assert msg["recipient"] == "+15551234"
        assert msg["text"] == "Hello from gateway"
        assert "id" in msg

        # The row should now be marked delivered
        row = GatewayMessage.objects.get(id=msg["id"])
        assert row.delivered_at is not None

    def test_second_get_returns_empty_after_first_claim(self, client, with_token):
        """
        GIVEN a message was already claimed by a previous GET
        WHEN GET /api/gateway/outbox is called again for the same platform
        THEN messages list is empty
        """
        enqueue_text("whatsapp", "+15551234", "Once only")

        client.get(f"{OUTBOX}?platform=whatsapp", **AUTH)
        response = client.get(f"{OUTBOX}?platform=whatsapp", **AUTH)

        assert response.status_code == 200
        assert response.data["messages"] == []

    def test_outbox_filters_by_platform(self, client, with_token):
        """
        GIVEN messages enqueued for whatsapp and slack
        WHEN GET /api/gateway/outbox?platform=slack
        THEN only the slack message is returned
        """
        enqueue_text("whatsapp", "w-user", "whatsapp msg")
        enqueue_text("slack", "s-channel", "slack msg")

        response = client.get(f"{OUTBOX}?platform=slack", **AUTH)
        assert response.status_code == 200
        assert len(response.data["messages"]) == 1
        assert response.data["messages"][0]["platform"] == "slack"

    def test_outbox_invalid_platform_returns_400(self, client, with_token):
        """
        GIVEN an invalid platform query param
        WHEN GET /api/gateway/outbox?platform=twitter
        THEN 400 is returned
        """
        response = client.get(f"{OUTBOX}?platform=twitter", **AUTH)
        assert response.status_code == 400

    def test_outbox_signal_platform_returns_200(self, client, with_token):
        """
        GIVEN a valid bearer token and platform=signal
        WHEN GET /api/gateway/outbox?platform=signal
        THEN 200 is returned (signal is a valid gateway platform)
        """
        response = client.get(f"{OUTBOX}?platform=signal", **AUTH)
        assert response.status_code == 200

    def test_outbox_imessage_platform_returns_200(self, client, with_token):
        """
        GIVEN a valid bearer token and platform=imessage
        WHEN GET /api/gateway/outbox?platform=imessage
        THEN 200 is returned (imessage is a valid gateway platform)
        """
        response = client.get(f"{OUTBOX}?platform=imessage", **AUTH)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Inbound — pending prompt resolved
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInboundWithPendingPrompt:
    def test_numbered_reply_resolves_choice_prompt_and_returns_recorded(
        self, client, with_token, thread
    ):
        """
        GIVEN a PENDING CHOICE_SINGLE Prompt on a thread linked to a GatewayChat
        WHEN POST /api/gateway/inbound with a numbered reply
        THEN the prompt is resolved and reply is "Recorded ✔"
        """
        from apps.gateway.models import GatewayChat

        GatewayChat.objects.create(platform="whatsapp", chat_id="+9991", thread=thread)

        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Pick one?",
            options=[{"key": "a", "label": "Option A"}, {"key": "b", "label": "Option B"}],
            ttl_seconds=900,
        )

        response = client.post(
            INBOUND,
            {"platform": "whatsapp", "chat_id": "+9991", "sender": "user1", "text": "1"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 200
        assert response.data["reply"] == "Recorded ✔"

        prompt.refresh_from_db()
        assert prompt.status == Prompt.StatusChoices.ANSWERED

    def test_invalid_reply_falls_through_to_dispatch(self, client, with_token, thread, monkeypatch):
        """
        GIVEN a PENDING Prompt on a thread
        WHEN POST /api/gateway/inbound with text that does not match any option
        THEN dispatch_text is called and its reply is returned
        """
        from apps.gateway.models import GatewayChat

        GatewayChat.objects.create(platform="whatsapp", chat_id="+9992", thread=thread)

        create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Pick one?",
            options=[{"key": "a", "label": "Option A"}],
            ttl_seconds=900,
        )

        dispatched = []

        async def fake_dispatch(t, text, *, on_event):
            dispatched.append(text)
            await on_event({"type": "message_complete", "text": "assistant reply"})

        monkeypatch.setattr("apps.gateway.service.dispatch_text", fake_dispatch)

        response = client.post(
            INBOUND,
            {
                "platform": "whatsapp",
                "chat_id": "+9992",
                "sender": "user2",
                "text": "random garbage xyz",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 200
        assert response.data["reply"] == "assistant reply"
        assert dispatched == ["random garbage xyz"]


# ---------------------------------------------------------------------------
# Inbound — no pending prompt, dispatches to LLM
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInboundDispatch:
    def test_no_pending_prompt_calls_dispatch_text_and_returns_reply(
        self, client, with_token, monkeypatch
    ):
        """
        GIVEN no PENDING Prompt exists for the chat
        WHEN POST /api/gateway/inbound with free text
        THEN dispatch_text is called and the message_complete text is returned
        """
        dispatched = []

        async def fake_dispatch(thread, text, *, on_event):
            dispatched.append(text)
            await on_event({"type": "message_complete", "text": "LLM says hello"})

        monkeypatch.setattr("apps.gateway.service.dispatch_text", fake_dispatch)

        response = client.post(
            INBOUND,
            {
                "platform": "discord",
                "chat_id": "channel-123",
                "sender": "user42",
                "text": "Hello world",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 200
        assert response.data["reply"] == "LLM says hello"
        assert dispatched == ["Hello world"]

    def test_inbound_bad_input_never_500(self, client, with_token):
        """
        GIVEN a completely empty body
        WHEN POST /api/gateway/inbound
        THEN 200 is returned with reply null (never 500)
        """
        response = client.post(INBOUND, {}, format="json", **AUTH)
        assert response.status_code == 200
        assert response.data["reply"] is None
