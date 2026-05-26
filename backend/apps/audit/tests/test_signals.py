import pytest

from apps.accounts.models import Account
from apps.approvals.models import ApprovalRequest
from apps.audit.models import AuditEvent
from apps.threads.models import Message, Thread


@pytest.mark.django_db
class TestAuditSignals:
    def test_thread_create_emits_audit_event(self):
        account = Account.objects.create(provider="anthropic", label="t", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="audit-thread", runtime="claude_code", account=account)
        event = AuditEvent.objects.filter(thread=thread, event_type=AuditEvent.EventTypeChoices.THREAD_CREATE).first()
        assert event is not None
        assert event.redacted_payload["name"] == "audit-thread"

    def test_message_send_emits_audit_event(self):
        account = Account.objects.create(provider="anthropic", label="m", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="audit-msg", runtime="claude_code", account=account)
        Message.objects.create(thread=thread, role="user", content="hello", sequence=1)
        event = AuditEvent.objects.filter(thread=thread, event_type=AuditEvent.EventTypeChoices.MESSAGE_SEND).first()
        assert event is not None
        assert event.redacted_payload["role"] == "user"
        assert event.redacted_payload["sequence"] == 1

    def test_approval_request_emits_audit_event(self):
        account = Account.objects.create(provider="anthropic", label="a", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="audit-appr", runtime="claude_code", account=account)
        from django.utils import timezone
        ApprovalRequest.objects.create(
            thread=thread,
            request_type="run_command",
            risk="high",
            summary="rm -rf /",
            expires_at=timezone.now(),
        )
        event = AuditEvent.objects.filter(
            thread=thread, event_type=AuditEvent.EventTypeChoices.APPROVAL_REQUEST
        ).first()
        assert event is not None
        assert event.redacted_payload["summary"] == "rm -rf /"

    def test_approval_grant_emits_audit_event(self):
        account = Account.objects.create(provider="anthropic", label="ag", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="audit-grant", runtime="claude_code", account=account)
        from django.utils import timezone
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type="push_branch",
            risk="low",
            summary="push",
            expires_at=timezone.now(),
        )
        approval.status = ApprovalRequest.StatusChoices.APPROVED
        approval.decided_by = "admin"
        approval.save()
        event = AuditEvent.objects.filter(
            thread=thread, event_type=AuditEvent.EventTypeChoices.APPROVAL_GRANT
        ).first()
        assert event is not None
        assert event.redacted_payload["status"] == "approved"
