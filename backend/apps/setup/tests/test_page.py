"""Tests for the /setup landing page and the token-for-cookie exchange (C5).

The installer opens /setup?token=<raw>. The first load rotates that token for
an HttpOnly, SameSite=Strict session cookie and redirects to a clean /setup —
this file verifies that exchange end to end, plus that the cookie is a valid
credential for the /api/setup/* routes.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.setup.models import SESSION_COOKIE_NAME, SetupState, SetupToken

SETUP_PAGE_URL = "/setup"
STATE_URL = "/api/setup/state"
ADVANCE_URL = "/api/setup/advance"
HOST = "localhost:8000"


@pytest.fixture
def client():
    return Client()


def _live_token() -> str:
    _obj, raw = SetupToken.issue()
    return raw


@pytest.mark.django_db
class TestSetupPageTokenExchange:
    def test_valid_token_redirects_with_exact_status_302(self, client):
        """
        GIVEN a freshly issued live setup token
        WHEN GET /setup?token=<raw> is requested
        THEN the response is a 302 redirect
        """
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.status_code == 302

    def test_redirect_location_has_no_query_string(self, client):
        """
        GIVEN a freshly issued live setup token
        WHEN GET /setup?token=<raw> is requested
        THEN the redirect Location has no query string
        """
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert "?" not in resp["Location"]

    def test_exchange_sets_httponly_cookie(self, client):
        """
        GIVEN a freshly issued live setup token
        WHEN GET /setup?token=<raw> is requested
        THEN the response sets the orc_setup_session cookie as HttpOnly
        """
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.cookies[SESSION_COOKIE_NAME]["httponly"] is True

    def test_exchange_sets_samesite_strict_cookie(self, client):
        """
        GIVEN a freshly issued live setup token
        WHEN GET /setup?token=<raw> is requested
        THEN the response sets the orc_setup_session cookie as SameSite=Strict
        """
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.cookies[SESSION_COOKIE_NAME]["samesite"] == "Strict"

    def test_cookie_value_is_not_the_url_token(self, client):
        """
        GIVEN a freshly issued live setup token used in the URL
        WHEN GET /setup?token=<raw> is requested
        THEN the cookie value differs from the URL token — it was rotated
        """
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.cookies[SESSION_COOKIE_NAME].value != raw

    def test_original_url_token_no_longer_works_after_exchange(self, client):
        """
        GIVEN a setup token that was used to complete the /setup exchange
        WHEN GET /api/setup/state is requested (via a cookie-less client) with
             that same original token
        THEN 403 is returned — issuing the cookie's token revoked it
        """
        raw = _live_token()
        client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        other_client = Client()
        resp = other_client.get(STATE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.status_code == 403

    def test_following_the_redirect_with_cookie_jar_returns_200(self, client):
        """
        GIVEN a completed /setup token exchange (cookie jar carries the cookie)
        WHEN GET /setup is requested again with no token
        THEN 200 is returned
        """
        raw = _live_token()
        client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert resp.status_code == 200

    def test_following_the_redirect_renders_the_stage_name(self, client):
        """
        GIVEN a completed /setup token exchange (cookie jar carries the cookie)
        WHEN GET /setup is requested again with no token
        THEN the rendered page contains the current stage name
        """
        raw = _live_token()
        client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert SetupState.STAGE_PROVIDERS.encode() in resp.content

    def test_bad_token_returns_403(self, client):
        """
        GIVEN no cookie session and an invalid token
        WHEN GET /setup?token=not-a-real-token is requested
        THEN 403 is returned
        """
        resp = client.get(SETUP_PAGE_URL, {"token": "not-a-real-token"}, HTTP_HOST=HOST)
        assert resp.status_code == 403

    def test_absent_token_returns_403(self, client):
        """
        GIVEN no cookie session and no token at all
        WHEN GET /setup is requested
        THEN 403 is returned
        """
        resp = client.get(SETUP_PAGE_URL, HTTP_HOST=HOST)
        assert resp.status_code == 403

    def test_valid_token_when_setup_already_complete_returns_410(self, client):
        """
        GIVEN a SetupState that has already completed setup
        WHEN GET /setup?token=<raw> is requested
        THEN 410 is returned
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        state.advance_to(SetupState.STAGE_DONE)
        raw = _live_token()
        resp = client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        assert resp.status_code == 410


@pytest.mark.django_db
class TestSetupSessionCookieAuthenticatesApi:
    def test_cookie_only_state_request_returns_200(self, client):
        """
        GIVEN a completed /setup token exchange (cookie jar carries the cookie)
        WHEN GET /api/setup/state is requested with no header and no query token
        THEN 200 is returned — the cookie alone authenticates the request
        """
        raw = _live_token()
        client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        resp = client.get(STATE_URL, HTTP_HOST=HOST)
        assert resp.status_code == 200

    def test_cookie_only_advance_post_returns_200(self, client):
        """
        GIVEN a completed /setup token exchange and a connected provider
        WHEN POST /api/setup/advance is requested with no header and no query
             token, authenticated only by the session cookie
        THEN 200 is returned — SameSite=Strict is what stops cross-site use,
             not a header requirement
        """
        raw = _live_token()
        client.get(SETUP_PAGE_URL, {"token": raw}, HTTP_HOST=HOST)
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        resp = client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_RUNTIMES},
            content_type="application/json",
            HTTP_HOST=HOST,
        )
        assert resp.status_code == 200
