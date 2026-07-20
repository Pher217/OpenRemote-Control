"""Tests for apps.setup.models: SetupToken lifecycle and the SetupState machine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.setup.models import SetupState, SetupToken, hash_token


@pytest.mark.django_db
class TestSetupTokenIssue:
    def test_issue_returns_raw_token_not_stored_anywhere(self):
        """
        GIVEN a fresh SetupToken.issue() call
        WHEN the raw token is compared against every stored token_hash
        THEN the raw value itself never appears in the database
        """
        obj, raw = SetupToken.issue()
        assert raw != obj.token_hash

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

    def test_advance_to_done_succeeds_after_provider_connected(self):
        """
        GIVEN a SetupState with a connected telegram provider
        WHEN advance_to("done") is called
        THEN the stage becomes "done"
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_DONE)
        assert state.stage == SetupState.STAGE_DONE

    def test_advance_to_done_sets_completed_at(self):
        """
        GIVEN a SetupState with a connected provider
        WHEN advance_to("done") is called
        THEN completed_at is no longer None
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_DONE)
        assert state.completed_at is not None

    def test_advance_to_done_sets_is_complete(self):
        """
        GIVEN a SetupState with a connected provider
        WHEN advance_to("done") is called
        THEN is_complete reports True
        """
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_DONE)
        assert state.is_complete is True

    def test_advance_to_providers_does_not_require_a_connected_provider(self):
        """
        GIVEN a fresh SetupState with no connected providers
        WHEN advance_to("providers") is called (the starting stage itself)
        THEN no exception is raised and the stage stays "providers"
        """
        state = SetupState.load()
        state.advance_to(SetupState.STAGE_PROVIDERS)
        assert state.stage == SetupState.STAGE_PROVIDERS


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
