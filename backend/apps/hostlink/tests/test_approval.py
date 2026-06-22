"""Tests for the host-authenticated tool-approval endpoint.

A driven session's SDK can_use_tool callback POSTs here to ask the operator to
approve a tool use; the approval is created on the session's thread and (in
production) delivered as Allow/Deny buttons to that thread's topic. The daemon
polls the result endpoint for the decision.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Account
from apps.hostlink.models import HostToken
from apps.hosts.models import Host
from apps.prompts.models import Prompt
from apps.prompts.service import resolve
from apps.threads.models import Thread

APPROVE_URL = "/api/hostlink/approve"


@pytest.fixture
def client():
    return APIClient()


def _account():
    return Account.objects.create(
        provider="connector", label="connector", auth_type="none", credential_type="none"
    )


def _host_with_token(slug):
    host = Host.objects.create(slug=slug, name=slug, os="linux")
    _, raw = HostToken.issue(host)
    return host, raw


def _thread(host):
    return Thread.objects.create(
        name="sess", runtime="claude_code", runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=_account(), host=host,
    )


@pytest.mark.django_db
def test_approve_creates_prompt_for_owned_thread(client, monkeypatch):
    """
    GIVEN a host-authed request for a thread the host owns
    WHEN POST /api/hostlink/approve
    THEN an APPROVAL prompt is created and a nonce returned (delivery best-effort).
    """
    monkeypatch.setattr("apps.connectors.service._deliver", lambda prompt: None)
    host, token = _host_with_token("ha1")
    thread = _thread(host)

    resp = client.post(
        APPROVE_URL,
        {"thread_id": str(thread.id), "title": "Claude wants to use Write: /x", "preview": "..."},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert resp.status_code == 201
    nonce = resp.json()["nonce"]
    prompt = Prompt.objects.get(nonce=nonce)
    assert prompt.thread_id == thread.id
    assert prompt.prompt_type == Prompt.PromptType.APPROVAL
    assert prompt.surface_message_ref["action"] == "sdk_permission"


@pytest.mark.django_db
def test_approve_rejects_thread_of_other_host(client, monkeypatch):
    """
    GIVEN a host-authed request for a thread owned by a DIFFERENT host
    WHEN POST /api/hostlink/approve
    THEN it is forbidden (no cross-host approvals).
    """
    monkeypatch.setattr("apps.connectors.service._deliver", lambda prompt: None)
    host_a, token_a = _host_with_token("ha2")
    host_b, _ = _host_with_token("ha3")
    other_thread = _thread(host_b)

    resp = client.post(
        APPROVE_URL,
        {"thread_id": str(other_thread.id), "title": "x"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token_a}",
    )

    assert resp.status_code == 403
    assert Prompt.objects.count() == 0


@pytest.mark.django_db
def test_approve_requires_auth(client):
    """GIVEN no host token WHEN POST /api/hostlink/approve THEN 401."""
    resp = client.post(APPROVE_URL, {"thread_id": "x"}, format="json")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_result_returns_decision_after_resolve(client, monkeypatch):
    """
    GIVEN an approval that the operator answered 'allow'
    WHEN the host polls the result endpoint
    THEN it returns the decision.
    """
    monkeypatch.setattr("apps.connectors.service._deliver", lambda prompt: None)
    host, token = _host_with_token("ha4")
    thread = _thread(host)
    nonce = client.post(
        APPROVE_URL, {"thread_id": str(thread.id), "title": "x"}, format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    ).json()["nonce"]

    resolve(nonce, option_keys=["allow"], by="op")

    resp = client.get(f"{APPROVE_URL}/{nonce}", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "answered", "decision": "allow"}
