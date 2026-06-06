from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt, get_by_nonce, resolve
from apps.threads.models import Thread


def make_account(suffix="svc1"):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-svc-{suffix}",
        credential_recipient=f"r-svc-{suffix}",
    )


def make_thread(account, name="svc-test"):
    return Thread.objects.create(
        name=name,
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=account,
    )


@pytest.mark.django_db
class TestCreatePrompt:
    def test_sets_nonce_and_hash(self):
        """GIVEN a thread WHEN create_prompt is called THEN nonce and prompt_hash are populated."""
        account = make_account("cp1")
        thread = make_thread(account, "cp-thread-1")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Proceed?",
            options=[{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}],
        )
        assert len(prompt.nonce) == 16
        assert len(prompt.prompt_hash) == 64

    def test_expires_at_is_in_future(self):
        """GIVEN a ttl_seconds WHEN create_prompt is called THEN expires_at is roughly now+ttl."""
        account = make_account("cp2")
        thread = make_thread(account, "cp-thread-2")
        before = timezone.now()
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.NOTICE,
            question="Session started",
            ttl_seconds=300,
        )
        after = timezone.now()
        assert prompt.expires_at >= before + timedelta(seconds=299)
        assert prompt.expires_at <= after + timedelta(seconds=301)

    def test_default_trust_class_approval_for_approval_type(self):
        """GIVEN prompt_type=APPROVAL WHEN trust_class not specified THEN trust_class=APPROVAL."""
        account = make_account("cp3")
        thread = make_thread(account, "cp-thread-3")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Deploy to prod?",
        )
        assert prompt.trust_class == Prompt.TrustClass.APPROVAL

    def test_default_trust_class_decision_for_other_types(self):
        """GIVEN prompt_type=CHOICE_SINGLE WHEN trust_class not specified THEN trust_class=DECISION."""
        account = make_account("cp4")
        thread = make_thread(account, "cp-thread-4")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Pick one",
            options=[{"key": "a", "label": "A"}],
        )
        assert prompt.trust_class == Prompt.TrustClass.DECISION

    def test_status_is_pending(self):
        """GIVEN a newly created prompt WHEN fetched THEN status is PENDING."""
        account = make_account("cp5")
        thread = make_thread(account, "cp-thread-5")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.FREE_TEXT,
            question="Describe the issue",
        )
        assert prompt.status == Prompt.StatusChoices.PENDING

    def test_persisted_to_db(self):
        """GIVEN create_prompt WHEN called THEN prompt exists in the database."""
        account = make_account("cp6")
        thread = make_thread(account, "cp-thread-6")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.NOTICE,
            question="Hello",
        )
        assert Prompt.objects.filter(pk=prompt.pk).exists()


@pytest.mark.django_db
class TestResolve:
    def test_records_response_and_returns_prompt(self):
        """GIVEN a PENDING prompt WHEN resolve is called with valid key THEN status=ANSWERED."""
        account = make_account("rv1")
        thread = make_thread(account, "rv-thread-1")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Allow?",
            options=[{"key": "allow", "label": "Allow"}, {"key": "deny", "label": "Deny"}],
        )
        result = resolve(prompt.nonce, option_keys=["allow"], by="user-42")
        assert result is not None
        assert result.pk == prompt.pk
        assert result.status == Prompt.StatusChoices.ANSWERED
        assert result.response == {"option_keys": ["allow"]}
        assert result.answered_by == "user-42"

    def test_anti_replay_second_call_returns_none(self):
        """GIVEN an already-answered prompt WHEN resolve is called again THEN returns None."""
        account = make_account("rv2")
        thread = make_thread(account, "rv-thread-2")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Allow?",
            options=[{"key": "allow", "label": "Allow"}],
        )
        resolve(prompt.nonce, option_keys=["allow"], by="user-1")
        second = resolve(prompt.nonce, option_keys=["allow"], by="user-2")
        assert second is None

    def test_expired_prompt_returns_none_and_marks_expired(self):
        """GIVEN an expired prompt WHEN resolve is called THEN returns None and status=EXPIRED."""
        account = make_account("rv3")
        thread = make_thread(account, "rv-thread-3")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Deploy?",
            ttl_seconds=1,
        )
        # Force expiry by setting expires_at in the past
        Prompt.objects.filter(pk=prompt.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = resolve(prompt.nonce, option_keys=["approve"], by="user-99")
        assert result is None
        prompt.refresh_from_db()
        assert prompt.status == Prompt.StatusChoices.EXPIRED

    def test_unknown_nonce_returns_none(self):
        """GIVEN a nonexistent nonce WHEN resolve is called THEN returns None."""
        result = resolve("deadbeefdeadbeef", option_keys=["yes"], by="user-1")
        assert result is None

    def test_invalid_option_key_returns_none(self):
        """GIVEN a PENDING prompt WHEN resolve is called with an unknown key THEN returns None."""
        account = make_account("rv4")
        thread = make_thread(account, "rv-thread-4")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Pick?",
            options=[{"key": "yes", "label": "Yes"}],
        )
        result = resolve(prompt.nonce, option_keys=["banana"], by="user-1")
        assert result is None
        # Prompt stays PENDING
        prompt.refresh_from_db()
        assert prompt.status == Prompt.StatusChoices.PENDING

    def test_exceeding_max_choices_returns_none(self):
        """GIVEN max_choices=1 WHEN resolve is called with 2 keys THEN returns None."""
        account = make_account("rv5")
        thread = make_thread(account, "rv-thread-5")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Pick one",
            options=[
                {"key": "a", "label": "A"},
                {"key": "b", "label": "B"},
            ],
            max_choices=1,
        )
        result = resolve(prompt.nonce, option_keys=["a", "b"], by="user-1")
        assert result is None


@pytest.mark.django_db
class TestGetByNonce:
    def test_returns_prompt_for_existing_nonce(self):
        """GIVEN an existing prompt WHEN get_by_nonce is called THEN returns it."""
        account = make_account("gn1")
        thread = make_thread(account, "gn-thread-1")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.NOTICE,
            question="Hello",
        )
        found = get_by_nonce(prompt.nonce)
        assert found is not None
        assert found.pk == prompt.pk

    def test_returns_none_for_missing_nonce(self):
        """GIVEN a nonexistent nonce WHEN get_by_nonce is called THEN returns None."""
        assert get_by_nonce("nonexistentnonce") is None
