"""Tests for the Stage-1 Telegram wizard body rendered by /setup (phase 2).

The page used to be a stub that only reported state (see test_page.py for the
token-exchange/session tests). These tests cover the rendered HTML body: the
right section shows up for the right stage, and the setup session token never
leaks into the markup — the whole point of the HttpOnly cookie exchange is
that the token is never script-readable or DOM-readable.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.setup.models import SetupState, SetupToken

SETUP_PAGE_URL = "/setup"
HOST = "localhost:8000"


@pytest.fixture
def client():
    return Client()


def _open_setup_session(client) -> str:
    """Mint a token, exchange it for a cookie session, return the raw token."""
    _obj, raw = SetupToken.issue()
    client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
    return raw


@pytest.mark.django_db
class TestTelegramCardRendersOnProvidersStage:
    def test_telegram_connect_card_present_on_providers_stage(self, client):
        """
        GIVEN a fresh setup session with the wizard on the providers stage
        WHEN GET /setup is requested
        THEN the rendered page contains the telegram-connect-card marker
        """
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b"telegram-connect-card" in resp.content

    def test_challenge_element_present_for_the_group_confirmation_step(self, client):
        """
        GIVEN a fresh setup session on the providers stage
        WHEN GET /setup is requested
        THEN the challenge-code element is rendered (populated client-side from
             the token response), so the operator has somewhere to read the code
             the discovery step will require the group to echo
        """
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b"telegram-challenge" in resp.content

    def test_runtimes_section_absent_on_providers_stage(self, client):
        """
        GIVEN a fresh setup session with the wizard on the providers stage
        WHEN GET /setup is requested
        THEN the rendered page does NOT contain the runtimes-section marker
        """
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b"runtimes-section" not in resp.content


@pytest.mark.django_db
class TestRuntimesSectionRendersOnRuntimesStage:
    def test_runtimes_section_present_on_runtimes_stage(self, client):
        """
        GIVEN a setup session with the wizard advanced to the runtimes stage
        WHEN GET /setup is requested
        THEN the rendered page contains the runtimes-section marker
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b"runtimes-section" in resp.content

    def test_telegram_connect_card_absent_on_runtimes_stage(self, client):
        """
        GIVEN a setup session with the wizard advanced to the runtimes stage
        WHEN GET /setup is requested
        THEN the rendered page does NOT contain the telegram-connect-card marker
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b"telegram-connect-card" not in resp.content


@pytest.mark.django_db
class TestSetupTokenNeverLeaksIntoMarkup:
    def test_raw_setup_token_absent_from_rendered_page(self, client):
        """
        GIVEN a setup session opened with a raw token (now rotated into an
             HttpOnly cookie by the exchange in page.py)
        WHEN GET /setup is requested
        THEN the raw token string does not appear anywhere in the response body
             — it must never be embedded in the page, since JS reading it back
             out of the DOM would defeat the whole point of the HttpOnly cookie
        """
        raw = _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert raw.encode() not in resp.content

    def test_cookie_session_value_absent_from_rendered_page(self, client):
        """
        GIVEN an established setup session cookie
        WHEN GET /setup is requested
        THEN the cookie's own value does not appear anywhere in the response
             body either — the wizard has no legitimate reason to echo its own
             session credential back into the page
        """
        client_ = client
        _raw = _open_setup_session(client_)
        cookie_value = client_.cookies["orc_setup_session"].value
        resp = client_.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert cookie_value.encode() not in resp.content


@pytest.mark.django_db
class TestNoUnsafeInnerHtmlAssignmentFromApiFields:
    def test_template_script_never_assigns_innerhtml_from_a_response_field(self, client):
        """
        GIVEN the rendered /setup page's inline script
        WHEN the script builds DOM nodes for values that came back from the
             /api/setup/telegram/* endpoints (e.g. a Telegram group title,
             which is attacker-influenced text controlled by whoever is in the
             group) — no such field is ever exercised server-side in this
             suite, since discover/token are stubs-under-construction owned by
             another worker in this same session
        THEN the template contains no `.innerHTML =` assignment at all, so
             there is no code path that could interpolate an API field as raw
             HTML; DOM construction instead goes through textContent /
             createElement, which is what actually prevents the injection
        """
        _open_setup_session(client)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert b".innerHTML" not in resp.content
