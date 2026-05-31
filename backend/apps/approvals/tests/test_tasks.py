from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.approvals.models import ApprovalRequest
from apps.approvals.tasks import expire_old_approval_requests
from apps.threads.models import Thread


@pytest.mark.django_db
class TestExpireOldApprovalRequests:
    def test_expires_pending_approvals(self):
        account = Account.objects.create(provider="anthropic", label="t", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="expiry-thread", runtime="claude_code", account=account)
        ApprovalRequest.objects.create(
            thread=thread,
            request_type="run_command",
            risk="high",
            summary="cmd",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        count = expire_old_approval_requests()
        assert count == 1
        assert ApprovalRequest.objects.filter(status=ApprovalRequest.StatusChoices.EXPIRED).count() == 1

    def test_leaves_future_approvals(self):
        account = Account.objects.create(provider="anthropic", label="t2", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="future-thread", runtime="claude_code", account=account)
        ApprovalRequest.objects.create(
            thread=thread,
            request_type="push_branch",
            risk="low",
            summary="push",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        count = expire_old_approval_requests()
        assert count == 0
        assert ApprovalRequest.objects.filter(status=ApprovalRequest.StatusChoices.PENDING).count() == 1
