from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.approvals.models import ApprovalRequest
from apps.threads.models import Thread


@pytest.mark.django_db
class TestApprovalRequestModel:
    def test_create_approval(self):
        """GIVEN a thread WHEN an approval is requested THEN it is pending."""
        account = Account.objects.create(
            provider="anthropic",
            label="test",
            auth_type="oauth",
            credential_type="token",
            encrypted_credential=b"z",
            credential_key_id="k4",
            credential_recipient="r4",
        )
        thread = Thread.objects.create(
            name="approval-test",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
        )
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type=ApprovalRequest.RequestTypeChoices.RUN_COMMAND,
            risk=ApprovalRequest.RiskChoices.HIGH,
            summary="rm -rf /",
            preview="Command: rm -rf /",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert approval.status == "pending"
        assert approval.risk == "high"
        assert approval.summary == "rm -rf /"

    def test_approval_expiry(self):
        """GIVEN an approval WHEN it expires THEN status is expired."""
        account = Account.objects.create(
            provider="openai",
            label="test",
            auth_type="api_key",
            credential_type="api_key",
            encrypted_credential=b"w",
            credential_key_id="k5",
            credential_recipient="r5",
        )
        thread = Thread.objects.create(
            name="expiry-test",
            runtime="codex",
            runtime_mode=Thread.RuntimeModeChoices.EXEC,
            account=account,
        )
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type=ApprovalRequest.RequestTypeChoices.PUSH_BRANCH,
            risk=ApprovalRequest.RiskChoices.LOW,
            summary="Push feature branch",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        assert approval.expires_at < timezone.now()
