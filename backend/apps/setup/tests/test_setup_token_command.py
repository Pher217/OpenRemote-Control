"""Tests for the `manage.py setup_token` management command."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.setup.models import SetupState, SetupToken


def _connect_provider():
    state = SetupState.load()
    state.set_provider("telegram", "connected")
    return state


def _complete_setup():
    """Walk the real providers -> runtimes -> done path and return the state."""
    state = _connect_provider()
    state.advance_to(SetupState.STAGE_RUNTIMES)
    state.advance_to(SetupState.STAGE_DONE)
    return state


@pytest.mark.django_db
class TestSetupTokenCommandFreshState:
    def test_prints_a_setup_url(self):
        """
        GIVEN no prior SetupState (fresh install)
        WHEN setup_token is run with no flags
        THEN the output contains a "/setup?token=" URL
        """
        out = StringIO()
        call_command("setup_token", stdout=out)
        assert "/setup?token=" in out.getvalue()

    def test_issues_a_live_token(self):
        """
        GIVEN no prior SetupState
        WHEN setup_token is run
        THEN exactly one SetupToken row exists and it is live
        """
        call_command("setup_token", stdout=StringIO())
        assert SetupToken.objects.count() == 1
        assert SetupToken.objects.first().is_live() is True

    def test_url_only_prints_only_the_url(self):
        """
        GIVEN no prior SetupState
        WHEN setup_token is run with --url-only
        THEN the output is exactly one line and it is the setup URL
        """
        out = StringIO()
        call_command("setup_token", "--url-only", stdout=out)
        lines = [line for line in out.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        assert "/setup?token=" in lines[0]

    def test_url_only_omits_the_human_banner(self):
        """
        GIVEN no prior SetupState
        WHEN setup_token is run with --url-only
        THEN the human-readable banner text is not present in the output
        """
        out = StringIO()
        call_command("setup_token", "--url-only", stdout=out)
        assert "Setup wizard ready" not in out.getvalue()


@pytest.mark.django_db
class TestSetupTokenCommandAlreadyComplete:
    def test_refuses_when_already_complete(self):
        """
        GIVEN a SetupState that is already complete
        WHEN setup_token is run without --reopen
        THEN an error message is written to stderr
        """
        _complete_setup()
        err = StringIO()
        call_command("setup_token", stdout=StringIO(), stderr=err)
        assert "already complete" in err.getvalue().lower()

    def test_issues_no_token_when_already_complete(self):
        """
        GIVEN a SetupState that is already complete
        WHEN setup_token is run without --reopen
        THEN no SetupToken row is created
        """
        _complete_setup()
        call_command("setup_token", stdout=StringIO(), stderr=StringIO())
        assert SetupToken.objects.count() == 0

    def test_reopen_clears_completion(self):
        """
        GIVEN a SetupState that is already complete
        WHEN setup_token is run with --reopen
        THEN SetupState.load().is_complete becomes False
        """
        _complete_setup()
        call_command("setup_token", "--reopen", stdout=StringIO())
        assert SetupState.load().is_complete is False

    def test_reopen_issues_a_new_token(self):
        """
        GIVEN a SetupState that is already complete
        WHEN setup_token is run with --reopen
        THEN a new live SetupToken is issued
        """
        _complete_setup()
        out = StringIO()
        call_command("setup_token", "--reopen", stdout=out)
        assert SetupToken.objects.count() == 1
        assert "/setup?token=" in out.getvalue()

    def test_reopen_clears_provider_status(self):
        """
        GIVEN a SetupState that is already complete with a connected provider
        WHEN setup_token is run with --reopen
        THEN SetupState.load().providers is empty (advisory status must be
             re-established, not carried across the reopen)
        """
        _complete_setup()
        call_command("setup_token", "--reopen", stdout=StringIO())
        assert SetupState.load().providers == {}


@pytest.mark.django_db
class TestSetupTokenCommandTTL:
    def test_ttl_flag_issues_token_expiring_about_90_minutes_out(self):
        """
        GIVEN no prior SetupState
        WHEN setup_token is run with --ttl 90
        THEN the issued token's expires_at is more than 89 minutes from now
        """
        call_command("setup_token", "--ttl", 90, stdout=StringIO())
        token = SetupToken.objects.first()
        assert token.expires_at > timezone.now() + timedelta(minutes=89)
