import pytest

from apps.accounts.models import Account
from apps.audit.models import AuditEvent
from apps.threads.models import Thread


@pytest.mark.django_db
class TestAuditEventModel:
    def test_create_audit_event(self):
        """GIVEN an action WHEN audited THEN redacted payload is stored."""
        account = Account.objects.create(
            provider="anthropic",
            label="test",
            auth_type="oauth",
            credential_type="token",
            encrypted_credential=b"a",
            credential_key_id="k6",
            credential_recipient="r6",
        )
        thread = Thread.objects.create(
            name="audit-test",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
        )
        event = AuditEvent.objects.create(
            thread=thread,
            actor="user:phil",
            event_type=AuditEvent.EventTypeChoices.THREAD_CREATE,
            redacted_payload={"thread_name": "audit-test"},
        )
        assert event.redacted_payload == {"thread_name": "audit-test"}
        assert event.raw_payload_encrypted is None
        assert event.raw_retention_expires_at is None

    def test_audit_event_without_thread(self):
        """GIVEN a system event WHEN no thread is involved THEN thread is null."""
        event = AuditEvent.objects.create(
            actor="system",
            event_type=AuditEvent.EventTypeChoices.RUNTIME_START,
            redacted_payload={"host": "localhost"},
        )
        assert event.thread is None
        assert event.actor == "system"
