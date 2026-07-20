"""Tests for the SetupTokenPermission gate on the /api/setup/* routes.

Covers the three checks in apps.setup.auth: setup-closed (410), host allowlist
(403), and one-time token (403) — plus the state-machine 400s reachable once
past the gate. Every assertion is an exact status code; never a range.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.setup.auth import normalise_host
from apps.setup.models import SetupState, SetupToken

STATE_URL = "/api/setup/state"
ADVANCE_URL = "/api/setup/advance"
COMPLETE_URL = "/api/setup/complete"


@pytest.fixture
def client():
    return APIClient()


def _live_token() -> str:
    _obj, raw = SetupToken.issue()
    return raw


def _live_token_pair() -> tuple[SetupToken, str]:
    return SetupToken.issue()


def _connect_provider():
    state = SetupState.load()
    state.set_provider("telegram", "connected")
    return state


def _ready_for_completion():
    """Connect a provider and walk to "runtimes" — the only stage from which
    the real state machine allows advancing to "done" (C1)."""
    state = _connect_provider()
    state.advance_to(SetupState.STAGE_RUNTIMES)
    return state


# ---------------------------------------------------------------------------
# Token presence / validity (403)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTokenGate:
    def test_no_token_returns_403(self, client):
        """
        GIVEN no token is supplied at all
        WHEN GET /api/setup/state is requested
        THEN 403 is returned
        """
        resp = client.get(STATE_URL, HTTP_HOST="localhost")
        assert resp.status_code == 403

    def test_wrong_token_returns_403(self, client):
        """
        GIVEN a live token exists
        WHEN GET /api/setup/state is requested with an unrelated token value
        THEN 403 is returned
        """
        SetupToken.issue()
        resp = client.get(STATE_URL, {"token": "totally-wrong-value"}, HTTP_HOST="localhost")
        assert resp.status_code == 403

    def test_expired_token_returns_403(self, client):
        """
        GIVEN a token whose expires_at is already in the past
        WHEN GET /api/setup/state is requested with that raw token
        THEN 403 is returned
        """
        obj, raw = SetupToken.issue()
        obj.expires_at = timezone.now() - timedelta(seconds=1)
        obj.save(update_fields=["expires_at"])
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="localhost")
        assert resp.status_code == 403

    def test_consumed_token_returns_403(self, client):
        """
        GIVEN a token that has already been consumed
        WHEN GET /api/setup/state is requested with that raw token
        THEN 403 is returned
        """
        obj, raw = SetupToken.issue()
        obj.consume()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="localhost")
        assert resp.status_code == 403

    def test_valid_token_in_query_param_returns_200(self, client):
        """
        GIVEN a freshly issued live token
        WHEN GET /api/setup/state is requested with ?token=<raw>
        THEN 200 is returned
        """
        raw = _live_token()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="localhost")
        assert resp.status_code == 200

    def test_valid_token_in_header_returns_200(self, client):
        """
        GIVEN a freshly issued live token
        WHEN GET /api/setup/state is requested with X-ORC-Setup-Token header
        THEN 200 is returned
        """
        raw = _live_token()
        resp = client.get(STATE_URL, HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Host allowlist (DNS-rebinding defense)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHostAllowlist:
    @override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "evil.example.com"])
    def test_disallowed_host_returns_403_even_with_valid_token(self, client):
        """
        GIVEN a valid, live setup token
        WHEN the request arrives with Host: evil.example.com (not in
             ORC_SETUP_ALLOWED_HOSTS, though Django's own ALLOWED_HOSTS
             permits it so the request reaches our permission check)
        THEN 403 is returned regardless of the valid token
        """
        raw = _live_token()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="evil.example.com")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# normalise_host — used for both the request Host and the allowlist entries
# ---------------------------------------------------------------------------


class TestNormaliseHost:
    def test_bracketed_ipv6_with_port_strips_to_bare_address(self):
        """
        GIVEN a bracketed IPv6 host with a port, "[::1]:8000"
        WHEN normalise_host is called
        THEN "::1" is returned
        """
        assert normalise_host("[::1]:8000") == "::1"

    def test_hostname_with_port_strips_to_bare_hostname(self):
        """
        GIVEN "localhost:8000"
        WHEN normalise_host is called
        THEN "localhost" is returned
        """
        assert normalise_host("localhost:8000") == "localhost"

    def test_uppercase_with_trailing_dot_is_lowercased_and_stripped(self):
        """
        GIVEN "LOCALHOST." (uppercase, trailing dot, no port)
        WHEN normalise_host is called
        THEN "localhost" is returned
        """
        assert normalise_host("LOCALHOST.") == "localhost"

    def test_bare_ipv4_is_unchanged(self):
        """
        GIVEN "127.0.0.1" with no port
        WHEN normalise_host is called
        THEN "127.0.0.1" is returned unchanged
        """
        assert normalise_host("127.0.0.1") == "127.0.0.1"


@pytest.mark.django_db
class TestNormaliseHostIntegration:
    @override_settings(ORC_SETUP_ALLOWED_HOSTS=["::1"], ALLOWED_HOSTS=["*"])
    def test_bracketed_ipv6_host_header_matches_bare_allowlist_entry(self, client):
        """
        GIVEN ORC_SETUP_ALLOWED_HOSTS contains the bare address "::1"
        WHEN GET /api/setup/state arrives with Host: [::1]:8000 and a valid
             token
        THEN 200 is returned (both sides are normalised before comparison)
        """
        raw = _live_token()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="[::1]:8000")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Setup-closed (410), regardless of credentials
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupClosed:
    def _complete_setup(self):
        state = _ready_for_completion()
        state.advance_to(SetupState.STAGE_DONE)

    def test_state_returns_410_with_valid_token_once_complete(self, client):
        """
        GIVEN setup has already been completed
        WHEN GET /api/setup/state is requested with a fresh valid token
        THEN 410 is returned
        """
        self._complete_setup()
        raw = _live_token()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="localhost")
        assert resp.status_code == 410

    def test_advance_returns_410_with_valid_token_once_complete(self, client):
        """
        GIVEN setup has already been completed
        WHEN POST /api/setup/advance is requested with a fresh valid token
        THEN 410 is returned
        """
        self._complete_setup()
        raw = _live_token()
        resp = client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_RUNTIMES},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        assert resp.status_code == 410

    def test_complete_returns_410_with_valid_token_once_complete(self, client):
        """
        GIVEN setup has already been completed
        WHEN POST /api/setup/complete is requested again with a fresh valid token
        THEN 410 is returned
        """
        self._complete_setup()
        raw = _live_token()
        resp = client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Completing the wizard burns the token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueryParamCsrfDefense:
    """POST must carry the token in a header; only GET/HEAD may use ?token=."""

    def test_post_advance_with_token_only_in_query_param_returns_403(self, client):
        """
        GIVEN a live token
        WHEN POST /api/setup/advance is requested with the token only in the
             actual query string (?token=<raw>), no header, and a body that
             carries only the stage
        THEN 403 is returned
        """
        _connect_provider()
        raw = _live_token()
        resp = client.post(
            f"{ADVANCE_URL}?token={raw}",
            {"stage": SetupState.STAGE_RUNTIMES},
            format="json",
            HTTP_HOST="localhost",
        )
        assert resp.status_code == 403

    def test_post_advance_with_token_in_header_returns_200(self, client):
        """
        GIVEN a live token
        WHEN POST /api/setup/advance is requested with the token in the
             X-ORC-Setup-Token header
        THEN 200 is returned
        """
        _connect_provider()
        raw = _live_token()
        resp = client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_RUNTIMES},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        assert resp.status_code == 200

    def test_get_state_with_token_in_query_param_still_returns_200(self, client):
        """
        GIVEN a live token
        WHEN GET /api/setup/state is requested with ?token= (a safe method)
        THEN 200 is returned
        """
        raw = _live_token()
        resp = client.get(STATE_URL, {"token": raw}, HTTP_HOST="localhost")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cross-site refusal (Sec-Fetch-Site / Origin)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossSiteRefusal:
    def test_cross_site_sec_fetch_site_header_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with Sec-Fetch-Site: cross-site
        THEN 403 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        assert resp.status_code == 403

    def test_disallowed_origin_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with Origin: http://evil.example.com
        THEN 403 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_ORIGIN="http://evil.example.com",
        )
        assert resp.status_code == 403

    def test_matching_origin_and_host_returns_200(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with Origin: http://localhost:8000
             and a matching Host: localhost:8000 (full netloc match — C3)
        THEN 200 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost:8000",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_ORIGIN="http://localhost:8000",
        )
        assert resp.status_code == 200

    def test_origin_on_a_different_loopback_port_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with Origin: http://localhost:3000
             (a dev server on another loopback port) and Host: localhost:8000
        THEN 403 is returned — a full netloc mismatch, even on loopback
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost:8000",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_ORIGIN="http://localhost:3000",
        )
        assert resp.status_code == 403

    def test_null_origin_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with Origin: null (sandboxed
             iframe or file://)
        THEN 403 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost:8000",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_ORIGIN="null",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Proxy header refusal (X-Forwarded-Host / X-Forwarded-For)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProxyHeaderRefusal:
    def test_x_forwarded_host_header_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with X-Forwarded-Host: localhost
        THEN 403 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_X_FORWARDED_HOST="localhost",
        )
        assert resp.status_code == 403

    def test_x_forwarded_for_header_returns_403(self, client):
        """
        GIVEN a valid header token
        WHEN GET /api/setup/state is requested with X-Forwarded-For: 1.2.3.4
        THEN 403 is returned
        """
        raw = _live_token()
        resp = client.get(
            STATE_URL,
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
            HTTP_X_FORWARDED_FOR="1.2.3.4",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /advance rejects stage="done" — completion must go through /complete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdvanceRejectsDone:
    def test_advance_with_stage_done_returns_400(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/advance is requested with stage="done"
        THEN 400 is returned
        """
        _connect_provider()
        raw = _live_token()
        resp = client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_DONE},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        assert resp.status_code == 400

    def test_advance_with_stage_done_does_not_complete_setup(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/advance is rejected for stage="done"
        THEN SetupState.load().is_complete is still False
        """
        _connect_provider()
        raw = _live_token()
        client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_DONE},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        assert SetupState.load().is_complete is False


@pytest.mark.django_db
class TestAdvanceDoesNotConsumeToken:
    def test_advance_to_runtimes_with_header_token_returns_200(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/advance is requested with stage="runtimes" and
             the token in the header
        THEN 200 is returned
        """
        _connect_provider()
        raw = _live_token()
        resp = client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_RUNTIMES},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        assert resp.status_code == 200

    def test_advance_to_runtimes_does_not_consume_the_token(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/advance succeeds for stage="runtimes"
        THEN the token row's consumed_at is still None afterward — only
             /complete burns the token
        """
        _connect_provider()
        obj, raw = _live_token_pair()
        client.post(
            ADVANCE_URL,
            {"stage": SetupState.STAGE_RUNTIMES},
            format="json",
            HTTP_HOST="localhost",
            HTTP_X_ORC_SETUP_TOKEN=raw,
        )
        obj.refresh_from_db()
        assert obj.consumed_at is None


@pytest.mark.django_db
class TestCompleteBurnsToken:
    def test_complete_with_valid_token_returns_200(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/complete is requested
        THEN 200 is returned
        """
        _ready_for_completion()
        raw = _live_token()
        resp = client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert resp.status_code == 200

    def test_complete_sets_is_complete_true(self, client):
        """
        GIVEN a connected provider and a live token
        WHEN POST /api/setup/complete succeeds
        THEN SetupState.load().is_complete is True afterward
        """
        _ready_for_completion()
        raw = _live_token()
        client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert SetupState.load().is_complete is True

    def test_second_call_with_same_token_returns_410(self, client):
        """
        GIVEN /api/setup/complete has already been called once with a token
        WHEN the exact same raw token is used again on /api/setup/complete
        THEN 410 is returned (setup is closed, not merely token-invalid)
        """
        _ready_for_completion()
        raw = _live_token()
        first = client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert first.status_code == 200
        second = client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert second.status_code == 410

    def test_complete_marks_the_token_row_consumed_in_the_database(self, client):
        """
        GIVEN a live token used to call /api/setup/complete successfully
        WHEN the SetupToken row is reloaded from the database afterward
        THEN its consumed_at column is no longer None
        """
        _ready_for_completion()
        obj, raw = SetupToken.issue()
        client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        obj.refresh_from_db()
        assert obj.consumed_at is not None

    def test_complete_makes_the_used_token_fail_verify(self, client):
        """
        GIVEN a live token used to call /api/setup/complete successfully
        WHEN SetupToken.verify() is called again with the same raw value
        THEN it returns None (the token itself was burned, not merely
             shadowed by the setup-closed check)
        """
        _ready_for_completion()
        _obj, raw = SetupToken.issue()
        client.post(
            COMPLETE_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
        )
        assert SetupToken.verify(raw) is None


@pytest.mark.django_db
class TestCheckOrdering:
    """The host gate runs before the setup-closed check, so an outsider cannot
    read 410-vs-403 to learn whether this installation is already set up."""

    @override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "evil.example.com"])
    def test_disallowed_host_returns_403_not_410_when_setup_is_complete(self, client):
        """
        GIVEN setup has been completed
        WHEN a request arrives from a host outside ORC_SETUP_ALLOWED_HOSTS
        THEN 403 is returned, not the 410 that would reveal completion
        """
        state = _connect_provider()
        state.advance_to(SetupState.STAGE_RUNTIMES)
        state.advance_to(SetupState.STAGE_DONE)
        resp = client.get(STATE_URL, {"token": _live_token()}, HTTP_HOST="evil.example.com")
        assert resp.status_code == 403

    def test_allowed_host_still_returns_410_when_setup_is_complete(self, client):
        """
        GIVEN setup has been completed
        WHEN a request arrives from an allowlisted loopback host
        THEN 410 is returned, so the legitimate operator still learns the state
        """
        state = _connect_provider()
        state.advance_to(SetupState.STAGE_RUNTIMES)
        state.advance_to(SetupState.STAGE_DONE)
        resp = client.get(STATE_URL, {"token": _live_token()}, HTTP_HOST="localhost")
        assert resp.status_code == 410
