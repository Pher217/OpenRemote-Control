"""Tests for apps.setup.models: SetupToken lifecycle and the SetupState machine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import models
from django.test import override_settings
from django.utils import timezone

from apps.setup.models import SetupState, SetupToken, hash_token


def _walk_to_done(state: SetupState) -> None:
    """Drive a fresh SetupState through the real providers->runtimes->done path."""
    state.set_provider("telegram", "connected")
    state.advance_to(SetupState.STAGE_RUNTIMES)
    state.advance_to(SetupState.STAGE_DONE)


@pytest.mark.django_db
class TestSetupTokenIssue:
    def test_issue_returns_raw_token_not_stored_anywhere(self):
        """
        GIVEN a fresh SetupToken.issue() call
        WHEN every char/text field value on the persisted row is inspected
        THEN the raw token value appears in none of them
        """
        obj, raw = SetupToken.issue()
        obj.refresh_from_db()
        text_fields = [
            field
            for field in obj._meta.get_fields()
            if isinstance(field, (models.CharField, models.TextField))
        ]
        assert text_fields, "expected at least one text-like field to check"
        for field in text_fields:
            value = getattr(obj, field.attname)
            assert raw not in str(value)

    def test_issue_stores_only_the_hash(self):
        """
        GIVEN a freshly issued token
        WHEN the stored row is inspected
        THEN token_hash equals the SHA-256 hex digest of the raw value
        """
        obj, raw = SetupToken.issue()
        assert obj.token_hash == hash_token(raw)

    def test_issue_revokes_previously_outstanding_tokens(self):
        """
        GIVEN one live token already issued
        WHEN issue() is called again
        THEN the first token's consumed_at is set (no longer live)
        """
        first, _raw1 = SetupToken.issue()
        SetupToken.issue()
        first.refresh_from_db()
        assert first.consumed_at is not None

    def test_issue_revoked_first_token_fails_verify(self):
        """
        GIVEN one live token already issued
        WHEN a second token is issued and the first raw value is verified
        THEN verify() returns None for the revoked first token
        """
        _first, raw1 = SetupToken.issue()
        SetupToken.issue()
        assert SetupToken.verify(raw1) is None

    def test_issue_new_token_is_live(self):
        """
        GIVEN issue() has just been called
        WHEN the returned object's is_live() is checked
        THEN it reports True
        """
        obj, _raw = SetupToken.issue()
        assert obj.is_live() is True


@pytest.mark.django_db
class TestSetupTokenVerify:
    def test_verify_valid_token_returns_the_token(self):
        """
        GIVEN a freshly issued live token
        WHEN verify() is called with the raw value
        THEN the matching SetupToken row is returned
        """
        obj, raw = SetupToken.issue()
        found = SetupToken.verify(raw)
        assert found is not None
        assert found.pk == obj.pk

    def test_verify_wrong_token_returns_none(self):
        """
        GIVEN a live token exists
        WHEN verify() is called with an unrelated random string
        THEN None is returned
        """
        SetupToken.issue()
        assert SetupToken.verify("not-the-real-token") is None

    def test_verify_empty_string_returns_none(self):
        """
        GIVEN a live token exists
        WHEN verify() is called with an empty string
        THEN None is returned
        """
        SetupToken.issue()
        assert SetupToken.verify("") is None

    def test_verify_expired_token_returns_none(self):
        """
        GIVEN a token whose expires_at is already in the past
        WHEN verify() is called with its raw value
        THEN None is returned
        """
        obj, raw = SetupToken.issue()
        obj.expires_at = timezone.now() - timedelta(seconds=1)
        obj.save(update_fields=["expires_at"])
        assert SetupToken.verify(raw) is None

    def test_verify_consumed_token_returns_none(self):
        """
        GIVEN a token that has been consumed
        WHEN verify() is called with its raw value
        THEN None is returned
        """
        obj, raw = SetupToken.issue()
        obj.consume()
        assert SetupToken.verify(raw) is None

    def test_verify_nonexistent_hash_returns_none(self):
        """
        GIVEN no token has ever been issued
        WHEN verify() is called with an arbitrary string
        THEN None is returned
        """
        assert SetupToken.verify("nothing-issued-yet") is None


@pytest.mark.django_db
class TestSetupTokenIsLiveBoundary:
    def test_is_live_true_just_before_expiry(self):
        """
        GIVEN a token expiring 1 second in the future
        WHEN is_live() is evaluated
        THEN it reports True
        """
        obj, _raw = SetupToken.issue()
        obj.expires_at = timezone.now() + timedelta(seconds=1)
        obj.save(update_fields=["expires_at"])
        assert obj.is_live() is True

    def test_is_live_false_exactly_at_expiry(self):
        """
        GIVEN a token whose expires_at equals "now"
        WHEN is_live(now=expires_at) is evaluated
        THEN it reports False (strict less-than boundary)
        """
        obj, _raw = SetupToken.issue()
        now = timezone.now()
        obj.expires_at = now
        obj.save(update_fields=["expires_at"])
        assert obj.is_live(now=now) is False

    def test_is_live_false_just_after_expiry(self):
        """
        GIVEN a token that expired 1 second ago
        WHEN is_live() is evaluated
        THEN it reports False
        """
        obj, _raw = SetupToken.issue()
        obj.expires_at = timezone.now() - timedelta(seconds=1)
        obj.save(update_fields=["expires_at"])
        assert obj.is_live() is False


@pytest.mark.django_db
class TestSetupTokenConsume:
    def test_consume_sets_consumed_at(self):
        """
        GIVEN a live token
        WHEN consume() is called
        THEN consumed_at is no longer None
        """
        obj, _raw = SetupToken.issue()
        obj.consume()
        assert obj.consumed_at is not None

    def test_consume_makes_verify_return_none(self):
        """
        GIVEN a live token
        WHEN it is consumed and then verified by its raw value
        THEN verify() returns None
        """
        obj, raw = SetupToken.issue()
        obj.consume()
        assert SetupToken.verify(raw) is None


@pytest.mark.django_db
class TestSetupStateSingleton:
    def test_load_creates_a_row_on_first_access(self):
        """
        GIVEN no SetupState row exists yet
        WHEN load() is called
        THEN exactly one row exists in the table
        """
        SetupState.load()
        assert SetupState.objects.count() == 1

    def test_load_twice_returns_same_pk(self):
        """
        GIVEN load() has already created the singleton row
        WHEN load() is called a second time
        THEN the same primary key is returned both times
        """
        first = SetupState.load()
        second = SetupState.load()
        assert first.pk == second.pk

    def test_load_twice_still_only_one_row(self):
        """
        GIVEN load() is called twice in a row
        WHEN the table is counted afterward
        THEN only one SetupState row exists
        """
        SetupState.load()
        SetupState.load()
        assert SetupState.objects.count() == 1


@pytest.mark.django_db
class TestSetupStateAdvanceTo:
    def test_advance_to_unknown_stage_raises_value_error(self):
        """
        GIVEN a fresh SetupState
        WHEN advance_to() is called with a stage name that doesn't exist
        THEN ValueError is raised
        """
        state = SetupState.load()
        with pytest.raises(ValueError):
            state.advance_to("not-a-real-stage")

    def test_advance_to_runtimes_without_provider_raises_value_error(self):
        """
        GIVEN a fresh SetupState with no connected providers
        WHEN advance_to("runtimes") is called
        THEN ValueError is raised
        """
        state = SetupState.load()
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_RUNTIMES)

    def test_advance_to_done_without_provider_raises_value_error(self):
        """
        GIVEN a fresh SetupState with no connected providers
        WHEN advance_to("done") is called
        THEN ValueError is raised
        """
        state = SetupState.load()
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_DONE)

    def test_advance_to_runtimes_succeeds_after_provider_connected(self):
        """
        GIVEN a SetupState with a connected telegram provider
        WHEN advance_to("runtimes") is called
        THEN the stage becomes "runtimes"
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        assert state.stage == SetupState.STAGE_RUNTIMES

    def test_advance_to_done_succeeds_after_walking_the_full_path(self):
        """
        GIVEN a SetupState walked providers -> runtimes with a connected provider
        WHEN advance_to("done") is called from "runtimes"
        THEN the stage becomes "done"
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        state.advance_to(SetupState.STAGE_DONE)
        assert state.stage == SetupState.STAGE_DONE

    def test_advance_to_done_sets_completed_at(self):
        """
        GIVEN a SetupState walked providers -> runtimes -> done
        WHEN advance_to("done") is called
        THEN completed_at is no longer None
        """
        state = SetupState.load()
        _walk_to_done(state)
        assert state.completed_at is not None

    def test_advance_to_done_sets_is_complete(self):
        """
        GIVEN a SetupState walked providers -> runtimes -> done
        WHEN advance_to("done") is called
        THEN is_complete reports True
        """
        state = SetupState.load()
        _walk_to_done(state)
        assert state.is_complete is True

    def test_advance_from_providers_directly_to_done_raises_value_error(self):
        """
        GIVEN a fresh SetupState with a connected provider, still at "providers"
        WHEN advance_to("done") is called (skipping "runtimes")
        THEN ValueError is raised — providers->done is not an allowed edge
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_DONE)

    def test_advance_to_providers_from_providers_raises_value_error(self):
        """
        GIVEN a fresh SetupState already at the "providers" stage
        WHEN advance_to("providers") is called again
        THEN ValueError is raised — providers->providers is not an allowed edge
        """
        state = SetupState.load()
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_PROVIDERS)


FORBIDDEN_EDGES = [
    (SetupState.STAGE_PROVIDERS, SetupState.STAGE_PROVIDERS),
    (SetupState.STAGE_PROVIDERS, SetupState.STAGE_DONE),
    (SetupState.STAGE_DONE, SetupState.STAGE_PROVIDERS),
    (SetupState.STAGE_DONE, SetupState.STAGE_RUNTIMES),
    (SetupState.STAGE_DONE, SetupState.STAGE_DONE),
    (SetupState.STAGE_RUNTIMES, SetupState.STAGE_RUNTIMES),
]

ALLOWED_EDGES = [
    (SetupState.STAGE_PROVIDERS, SetupState.STAGE_RUNTIMES),
    (SetupState.STAGE_RUNTIMES, SetupState.STAGE_PROVIDERS),
    (SetupState.STAGE_RUNTIMES, SetupState.STAGE_DONE),
]


@pytest.mark.django_db
@pytest.mark.parametrize("from_stage,to_stage", FORBIDDEN_EDGES)
class TestSetupStateForbiddenTransitions:
    def test_forbidden_edge_raises_value_error(self, from_stage, to_stage):
        """
        GIVEN a SetupState sitting at from_stage
        WHEN advance_to(to_stage) is called for an edge outside ALLOWED_TRANSITIONS
        THEN ValueError is raised
        """
        state = SetupState.load()
        state.stage = from_stage
        state.save(update_fields=["stage"])
        with pytest.raises(ValueError):
            state.advance_to(to_stage)


@pytest.mark.django_db
@pytest.mark.parametrize("from_stage,to_stage", ALLOWED_EDGES)
class TestSetupStateAllowedTransitions:
    def test_allowed_edge_succeeds(self, from_stage, to_stage):
        """
        GIVEN a SetupState sitting at from_stage with a connected provider
        WHEN advance_to(to_stage) is called for an edge inside ALLOWED_TRANSITIONS
        THEN the stage becomes to_stage
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.stage = from_stage
        state.save(update_fields=["stage"])
        state.advance_to(to_stage)
        assert state.stage == to_stage


@pytest.mark.django_db
class TestSetupStateAdvanceToDoneRequiresProvider:
    def test_advance_to_done_from_runtimes_without_provider_raises_value_error(self):
        """
        GIVEN a SetupState at "runtimes" with no connected provider
        WHEN advance_to("done") is called
        THEN ValueError is raised regardless of the transition being allowed
        """
        state = SetupState.load()
        state.stage = SetupState.STAGE_RUNTIMES
        state.save(update_fields=["stage"])
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_DONE)


@pytest.mark.django_db
class TestSetupStateReopen:
    def test_reopen_after_completion_resets_stage_to_providers(self):
        """
        GIVEN a SetupState that has completed setup
        WHEN reopen() is called
        THEN stage is reset to "providers"
        """
        state = SetupState.load()
        _walk_to_done(state)
        state.reopen()
        assert state.stage == SetupState.STAGE_PROVIDERS

    def test_reopen_after_completion_clears_completed_at(self):
        """
        GIVEN a SetupState that has completed setup
        WHEN reopen() is called
        THEN completed_at becomes None
        """
        state = SetupState.load()
        _walk_to_done(state)
        state.reopen()
        assert state.completed_at is None

    def test_reopen_after_completion_clears_providers(self):
        """
        GIVEN a SetupState that has completed setup with a connected provider
        WHEN reopen() is called
        THEN the providers dict is empty
        """
        state = SetupState.load()
        _walk_to_done(state)
        state.reopen()
        assert state.providers == {}

    def test_reopen_after_completion_clears_runtimes(self):
        """
        GIVEN a SetupState that has completed setup
        WHEN reopen() is called
        THEN the runtimes dict is empty
        """
        state = SetupState.load()
        _walk_to_done(state)
        state.set_runtime("claude_code", "detected")
        state.reopen()
        assert state.runtimes == {}

    def test_reopen_then_advance_to_runtimes_raises_value_error(self):
        """
        GIVEN a SetupState that was completed and then reopened
        WHEN advance_to("runtimes") is called
        THEN ValueError is raised because reopen() cleared the provider status
        """
        state = SetupState.load()
        _walk_to_done(state)
        state.reopen()
        with pytest.raises(ValueError):
            state.advance_to(SetupState.STAGE_RUNTIMES)


@pytest.mark.django_db
class TestSetupTokenTTLFromSettings:
    def test_ttl_override_expires_at_least_4_minutes_out(self):
        """
        GIVEN ORC_SETUP_TOKEN_TTL_MINUTES=5 via override_settings
        WHEN a token is issued
        THEN expires_at is more than 4 minutes from now
        """
        with override_settings(ORC_SETUP_TOKEN_TTL_MINUTES=5):
            obj, _raw = SetupToken.issue()
        assert obj.expires_at > timezone.now() + timedelta(minutes=4)

    def test_ttl_override_expires_less_than_6_minutes_out(self):
        """
        GIVEN ORC_SETUP_TOKEN_TTL_MINUTES=5 via override_settings
        WHEN a token is issued
        THEN expires_at is less than 6 minutes from now
        """
        with override_settings(ORC_SETUP_TOKEN_TTL_MINUTES=5):
            obj, _raw = SetupToken.issue()
        assert obj.expires_at < timezone.now() + timedelta(minutes=6)

    def test_issue_ttl_argument_overrides_settings(self):
        """
        GIVEN ORC_SETUP_TOKEN_TTL_MINUTES=5 via override_settings
        WHEN issue(ttl=timedelta(minutes=90)) is called
        THEN expires_at is more than 89 minutes from now — the explicit ttl wins
        """
        with override_settings(ORC_SETUP_TOKEN_TTL_MINUTES=5):
            obj, _raw = SetupToken.issue(ttl=timedelta(minutes=90))
        assert obj.expires_at > timezone.now() + timedelta(minutes=89)


@pytest.mark.django_db
class TestSetupStateConnectedProviders:
    def test_connected_providers_lists_only_connected_status(self):
        """
        GIVEN providers with a mix of "connected" and other statuses
        WHEN connected_providers is read
        THEN only the keys whose value is exactly "connected" are listed
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.set_provider("whatsapp", "pending")
        assert state.connected_providers == ["telegram"]

    def test_connected_providers_empty_when_none_connected(self):
        """
        GIVEN a fresh SetupState with no providers set
        WHEN connected_providers is read
        THEN an empty list is returned
        """
        state = SetupState.load()
        assert state.connected_providers == []

    def test_connected_providers_excludes_falsy_but_nonconnected_status(self):
        """
        GIVEN a provider whose status is an empty string
        WHEN connected_providers is read
        THEN that provider is not listed
        """
        state = SetupState.load()
        state.set_provider("slack", "")
        assert state.connected_providers == []


class TestNormaliseSetupHostGuard:
    def test_bracketed_ipv6_with_port_strips_to_bare_address(self):
        """
        GIVEN "[::1]:8000"
        WHEN config.settings.base._normalise_setup_host is called
        THEN "::1" is returned
        """
        from config.settings.base import _normalise_setup_host

        assert _normalise_setup_host("[::1]:8000") == "::1"

    def test_hostname_with_port_strips_to_bare_hostname(self):
        """
        GIVEN "localhost:8000"
        WHEN config.settings.base._normalise_setup_host is called
        THEN "localhost" is returned
        """
        from config.settings.base import _normalise_setup_host

        assert _normalise_setup_host("localhost:8000") == "localhost"

    def test_uppercase_with_trailing_dot_is_lowercased_and_stripped(self):
        """
        GIVEN "LOCALHOST." (uppercase, trailing dot, no port)
        WHEN config.settings.base._normalise_setup_host is called
        THEN "localhost" is returned
        """
        from config.settings.base import _normalise_setup_host

        assert _normalise_setup_host("LOCALHOST.") == "localhost"


class TestProductionRedirectExemption:
    """The setup wizard must survive SECURE_SSL_REDIRECT in production (C5).

    Reads config/settings/production.py as text rather than importing it —
    importing would re-execute config/settings/base.py's module-level env
    validation, which is unnecessary risk for a test that only needs to know
    the exempt-pattern strings are present.
    """

    def _production_settings_source(self) -> str:
        import importlib.util

        spec = importlib.util.find_spec("config.settings.production")
        assert spec is not None and spec.origin is not None
        return open(spec.origin, encoding="utf-8").read()

    def test_setup_page_pattern_is_redirect_exempt(self):
        """
        GIVEN the source of config/settings/production.py
        WHEN it is inspected for SECURE_REDIRECT_EXEMPT
        THEN the "^setup/?$" pattern is present
        """
        source = self._production_settings_source()
        assert r"^setup/?$" in source

    def test_setup_api_pattern_is_redirect_exempt(self):
        """
        GIVEN the source of config/settings/production.py
        WHEN it is inspected for SECURE_REDIRECT_EXEMPT
        THEN the "^api/setup/" pattern is present
        """
        source = self._production_settings_source()
        assert r"^api/setup/" in source
