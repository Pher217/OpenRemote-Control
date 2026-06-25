"""
Tests for the connector bridge API endpoints.

Telegram delivery is patched to a no-op async so no network calls are made.
"""

import pytest
from rest_framework.test import APIClient

from apps.connectors.models import ConnectorInstance
from apps.prompts.models import Prompt
from apps.prompts.service import resolve
from apps.threads.models import Message

TOKEN = "test-connector-token-abc"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
BASE = "/api/connectors"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture(autouse=True)
def patch_telegram(monkeypatch):
    """Replace telegram send_message with a no-op coroutine."""
    import apps.telegram.telegram_api as tg_api

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tg_api, "send_message", _noop)


@pytest.fixture
def with_token(settings):
    settings.ORC_CONNECTOR_TOKEN = TOKEN
    settings.ORC_PROMPT_CHAT_ID = ""
    return TOKEN


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConnectorAuth:
    def test_missing_token_returns_401(self, client, settings):
        """
        GIVEN ORC_CONNECTOR_TOKEN is set but no Authorization header is sent
        WHEN POST /api/connectors/notify is called
        THEN 401 is returned
        """
        settings.ORC_CONNECTOR_TOKEN = TOKEN
        settings.ORC_PROMPT_CHAT_ID = ""
        response = client.post(
            f"{BASE}/notify",
            {"connector_id": "c1", "tool": "claude", "message": "hi"},
            format="json",
        )
        assert response.status_code == 401

    def test_wrong_token_returns_401(self, client, settings):
        """
        GIVEN ORC_CONNECTOR_TOKEN is set and a wrong token is provided
        WHEN POST /api/connectors/notify is called
        THEN 401 is returned
        """
        settings.ORC_CONNECTOR_TOKEN = TOKEN
        settings.ORC_PROMPT_CHAT_ID = ""
        response = client.post(
            f"{BASE}/notify",
            {"connector_id": "c1", "tool": "claude", "message": "hi"},
            format="json",
            **{"HTTP_AUTHORIZATION": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_unconfigured_token_returns_503(self, client, settings):
        """
        GIVEN ORC_CONNECTOR_TOKEN is empty (not configured)
        WHEN any endpoint is called
        THEN 503 is returned
        """
        settings.ORC_CONNECTOR_TOKEN = ""
        settings.ORC_PROMPT_CHAT_ID = ""
        response = client.post(
            f"{BASE}/notify",
            {"connector_id": "c1", "tool": "claude", "message": "hi"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# start (the /openremote-control dispatch endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStart:
    def test_start_creates_named_session_and_binds_connector(self, client, with_token):
        """
        GIVEN a valid connector token
        WHEN POST /start with a session name
        THEN 201 {ok, thread_id, name} is returned and the connector is bound to it
        """
        response = client.post(
            f"{BASE}/start",
            {
                "connector_id": "conn-start-1",
                "tool": "claude_code",
                "workspace_root": "/home/user/project",
                "name": "Nightly deploy",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        assert response.data["ok"] is True
        assert response.data["name"] == "Nightly deploy"

        instance = ConnectorInstance.objects.get(connector_id="conn-start-1")
        assert str(instance.thread_id) == response.data["thread_id"]
        assert instance.thread.name == "Nightly deploy"

    def test_start_creates_driveable_headless_session_when_host_enrolled(self, client, with_token):
        """
        GIVEN a host daemon is enrolled
        WHEN POST /start dispatches a session
        THEN the thread is DRIVEABLE (headless metadata + bound host + PTY mode +
             claude_session_id + cwd), so a typed reply routes to `claude -p` —
             never a read-only chat. (chats-must-be-write-and-stream requirement)
        """
        from apps.hosts.models import Host
        from apps.threads.models import Thread

        Host.objects.create(slug="drv-host", name="DrvHost", os="darwin")
        response = client.post(
            f"{BASE}/start",
            {
                "connector_id": "conn-drv",
                "tool": "claude",
                "workspace_root": "/home/user/proj",
                "name": "Work",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        t = Thread.objects.get(id=response.data["thread_id"])
        md = t.metadata or {}
        assert t.host_id is not None
        assert md.get("headless") is True
        assert md.get("cwd") == "/home/user/proj"
        assert md.get("claude_session_id")
        assert t.runtime_mode == Thread.RuntimeModeChoices.PTY

    def test_start_falls_back_to_readonly_when_host_ambiguous(self, client, with_token):
        """
        GIVEN more than one enrolled host (can't tell which machine the workspace is on)
        WHEN POST /start dispatches a session
        THEN it does NOT guess a host — the thread is the read-only API fallback,
             never bound to (and executable on) the wrong machine.
        """
        from apps.hosts.models import Host
        from apps.threads.models import Thread

        Host.objects.create(slug="h-a", name="HostA", os="darwin")
        Host.objects.create(slug="h-b", name="HostB", os="linux")
        response = client.post(
            f"{BASE}/start",
            {"connector_id": "conn-amb", "tool": "claude", "workspace_root": "/x", "name": "Amb"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        t = Thread.objects.get(id=response.data["thread_id"])
        assert t.host_id is None
        assert (t.metadata or {}).get("headless") is not True
        assert t.runtime_mode == Thread.RuntimeModeChoices.API

    def test_start_rebinds_existing_connector_to_new_thread(self, client, with_token):
        """
        GIVEN a connector already bound to a thread (via /notify)
        WHEN POST /start is called
        THEN the connector is rebound to a fresh thread
        """
        client.post(
            f"{BASE}/notify",
            {"connector_id": "conn-start-2", "tool": "claude_code", "message": "hi"},
            format="json",
            **AUTH,
        )
        first = ConnectorInstance.objects.get(connector_id="conn-start-2").thread_id

        response = client.post(
            f"{BASE}/start",
            {"connector_id": "conn-start-2", "tool": "claude_code", "name": "Fresh"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        rebound = ConnectorInstance.objects.get(connector_id="conn-start-2").thread_id
        assert str(rebound) == response.data["thread_id"]
        assert rebound != first

    def test_start_auto_names_when_name_blank(self, client, with_token):
        """
        GIVEN no name in the body
        WHEN POST /start
        THEN the session gets an auto-generated name (non-empty)
        """
        response = client.post(
            f"{BASE}/start",
            {"connector_id": "conn-start-3", "tool": "claude_code"},
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        assert response.data["name"]


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNotify:
    def test_notify_returns_ok_and_persists_message(self, client, with_token):
        """
        GIVEN a valid connector token
        WHEN POST /notify with connector_id, tool, message
        THEN 200 {ok: true} is returned and a SYSTEM_EVENT Message is persisted
        """
        response = client.post(
            f"{BASE}/notify",
            {
                "connector_id": "conn-notify-1",
                "tool": "claude_code",
                "workspace_root": "/home/user/project",
                "message": "Build started",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 200
        assert response.data == {"ok": True}

        instance = ConnectorInstance.objects.get(connector_id="conn-notify-1")
        assert instance.tool == "claude_code"

        msg = Message.objects.filter(thread=instance.thread, role="system_event").first()
        assert msg is not None
        assert msg.redacted_content == "Build started"


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAsk:
    def test_ask_returns_201_with_nonce_and_pending_prompt(self, client, with_token):
        """
        GIVEN a valid connector token
        WHEN POST /ask with question and options
        THEN 201 {nonce, status:'pending'} and a PENDING Prompt exist
        """
        response = client.post(
            f"{BASE}/ask",
            {
                "connector_id": "conn-ask-1",
                "tool": "claude_code",
                "question": "Which branch?",
                "options": ["main", "develop", "feature/x"],
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        nonce = response.data["nonce"]
        assert nonce

        prompt = Prompt.objects.get(nonce=nonce)
        assert prompt.status == Prompt.StatusChoices.PENDING
        assert prompt.prompt_type == Prompt.PromptType.CHOICE_SINGLE

    def test_ask_without_options_creates_free_text_prompt(self, client, with_token):
        """
        GIVEN options is an empty list
        WHEN POST /ask
        THEN prompt_type is FREE_TEXT
        """
        response = client.post(
            f"{BASE}/ask",
            {
                "connector_id": "conn-ask-2",
                "tool": "claude_code",
                "question": "Any notes?",
                "options": [],
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        nonce = response.data["nonce"]
        prompt = Prompt.objects.get(nonce=nonce)
        assert prompt.prompt_type == Prompt.PromptType.FREE_TEXT


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApprove:
    def test_approve_returns_201_with_nonce_and_pending_prompt(self, client, with_token):
        """
        GIVEN a valid connector token
        WHEN POST /approve with action and preview
        THEN 201 {nonce, status:'pending'} and a PENDING APPROVAL Prompt exist
        """
        response = client.post(
            f"{BASE}/approve",
            {
                "connector_id": "conn-approve-1",
                "tool": "claude_code",
                "action": "git push origin main",
                "preview": "Pushes 3 commits to main branch",
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        assert response.data["status"] == "pending"
        nonce = response.data["nonce"]

        prompt = Prompt.objects.get(nonce=nonce)
        assert prompt.status == Prompt.StatusChoices.PENDING
        assert prompt.prompt_type == Prompt.PromptType.APPROVAL


# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResult:
    def test_result_pending(self, client, with_token):
        """
        GIVEN a pending prompt was created via /ask
        WHEN GET /result/<nonce>
        THEN {status:'pending'} is returned
        """
        ask_response = client.post(
            f"{BASE}/ask",
            {
                "connector_id": "conn-result-1",
                "tool": "claude_code",
                "question": "Confirm?",
                "options": ["yes", "no"],
            },
            format="json",
            **AUTH,
        )
        nonce = ask_response.data["nonce"]

        result_response = client.get(f"{BASE}/result/{nonce}", **AUTH)
        assert result_response.status_code == 200
        assert result_response.data["status"] == "pending"

    def test_result_unknown_nonce_returns_expired(self, client, with_token):
        """
        GIVEN a nonce that does not exist
        WHEN GET /result/<nonce>
        THEN {status:'expired'} is returned
        """
        response = client.get(f"{BASE}/result/nonexistentnonce", **AUTH)
        assert response.status_code == 200
        assert response.data["status"] == "expired"

    def test_approve_result_decision_allow(self, client, with_token):
        """
        GIVEN an approval prompt was created
        WHEN prompts.service.resolve is called with option_keys=['allow']
        AND GET /result/<nonce> is called
        THEN {status:'answered', decision:'allow'} is returned
        """
        approve_response = client.post(
            f"{BASE}/approve",
            {
                "connector_id": "conn-result-2",
                "tool": "claude_code",
                "action": "deploy to prod",
                "preview": "Rolling deploy",
            },
            format="json",
            **AUTH,
        )
        nonce = approve_response.data["nonce"]

        resolved = resolve(nonce, option_keys=["allow"], by="test")
        assert resolved is not None

        result_response = client.get(f"{BASE}/result/{nonce}", **AUTH)
        assert result_response.status_code == 200
        assert result_response.data["status"] == "answered"
        assert result_response.data["decision"] == "allow"

    def test_ask_result_answer(self, client, with_token):
        """
        GIVEN an ask prompt was created with options
        WHEN resolve is called with option_keys=['main']
        AND GET /result/<nonce> is called
        THEN {status:'answered', answer:'main'} is returned
        """
        ask_response = client.post(
            f"{BASE}/ask",
            {
                "connector_id": "conn-result-3",
                "tool": "claude_code",
                "question": "Which branch?",
                "options": ["main", "develop"],
            },
            format="json",
            **AUTH,
        )
        nonce = ask_response.data["nonce"]

        resolved = resolve(nonce, option_keys=["main"], by="test")
        assert resolved is not None

        result_response = client.get(f"{BASE}/result/{nonce}", **AUTH)
        assert result_response.status_code == 200
        assert result_response.data["status"] == "answered"
        assert result_response.data["answer"] == "main"
