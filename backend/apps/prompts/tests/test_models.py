from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.prompts.models import Prompt
from apps.threads.models import Thread


def make_account(suffix="1"):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-prompt-{suffix}",
        credential_recipient=f"r-prompt-{suffix}",
    )


def make_thread(account, name="prompt-test"):
    return Thread.objects.create(
        name=name,
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=account,
    )


@pytest.mark.django_db
class TestPromptModel:
    def test_notice_prompt_persists(self):
        """GIVEN a NOTICE prompt WHEN saved THEN it persists with PENDING status."""
        account = make_account("n1")
        thread = make_thread(account, "notice-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.NOTICE,
            question="Session started on host-a",
            nonce="abc123",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert prompt.status == Prompt.StatusChoices.PENDING
        assert prompt.prompt_type == "notice"
        assert Prompt.objects.filter(pk=prompt.pk).exists()

    def test_choice_single_options_round_trip(self):
        """GIVEN a CHOICE_SINGLE prompt with options WHEN saved THEN options JSON round-trips."""
        account = make_account("c1")
        thread = make_thread(account, "choice-thread")
        options = [
            {"key": "yes", "label": "Yes"},
            {"key": "no", "label": "No"},
        ]
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Approve deployment?",
            options=options,
            nonce="def456",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        prompt.refresh_from_db()
        assert prompt.options == options
        assert prompt.options[0]["key"] == "yes"
        assert prompt.options[1]["label"] == "No"

    def test_default_trust_class_is_decision(self):
        """GIVEN a prompt with no explicit trust_class WHEN created THEN trust_class defaults to DECISION."""
        account = make_account("d1")
        thread = make_thread(account, "trust-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.FREE_TEXT,
            question="What is your intent?",
            nonce="ghi789",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert prompt.trust_class == Prompt.TrustClass.DECISION
        assert prompt.trust_class == "decision"

    def test_record_response_sets_answered_state(self):
        """GIVEN a PENDING prompt WHEN record_response is called THEN status is ANSWERED and fields are set."""
        account = make_account("r1")
        thread = make_thread(account, "response-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Allow network access?",
            options=[{"key": "allow", "label": "Allow"}],
            nonce="jkl012",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert prompt.status == "pending"

        prompt.record_response(option_keys=["allow"], by="phil@example.com")

        assert prompt.status == Prompt.StatusChoices.ANSWERED
        assert prompt.response == {"option_keys": ["allow"]}
        assert prompt.answered_by == "phil@example.com"
        assert prompt.answered_at is not None
        # record_response must not auto-save; DB still shows pending
        prompt_db = Prompt.objects.get(pk=prompt.pk)
        assert prompt_db.status == "pending"

    def test_record_response_free_text(self):
        """GIVEN a FREE_TEXT prompt WHEN record_response called with text THEN response holds text."""
        account = make_account("r2")
        thread = make_thread(account, "freetext-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.FREE_TEXT,
            question="Describe the issue",
            nonce="mno345",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        prompt.record_response(text="connection refused on port 443", by="agent-1")
        assert prompt.response == {"text": "connection refused on port 443"}
        assert prompt.status == "answered"

    def test_is_expired_returns_true_past_expires_at(self):
        """GIVEN a prompt with expires_at in the past WHEN is_expired called THEN returns True."""
        account = make_account("e1")
        thread = make_thread(account, "expired-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Deploy to production?",
            nonce="pqr678",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        assert prompt.is_expired(timezone.now()) is True

    def test_is_expired_returns_false_before_expires_at(self):
        """GIVEN a prompt with expires_at in the future WHEN is_expired called THEN returns False."""
        account = make_account("e2")
        thread = make_thread(account, "live-thread")
        prompt = Prompt.objects.create(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Deploy to staging?",
            nonce="stu901",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert prompt.is_expired(timezone.now()) is False
