from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.audit.models import AuditEvent
from apps.audit.tasks import cleanup_old_audit_events
from apps.threads.models import Thread


@pytest.mark.django_db
class TestCleanupOldAuditEvents:
    def test_deletes_old_events(self):
        account = Account.objects.create(provider="anthropic", label="t", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="old-thread", runtime="claude_code", account=account)
        # Thread creation signal creates an audit event automatically.
        # We create another one and backdate it manually.
        event = AuditEvent.objects.create(
            thread=thread, actor="user", event_type="thread_create",
        )
        event.timestamp = timezone.now() - timedelta(days=100)
        event.save(update_fields=["timestamp"])
        deleted = cleanup_old_audit_events(retention_days=90)
        assert deleted == 1
        # The auto-created event from thread signal remains.
        assert AuditEvent.objects.count() == 1

    def test_keeps_recent_events(self):
        account = Account.objects.create(provider="anthropic", label="t2", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="recent-thread", runtime="claude_code", account=account)
        event = AuditEvent.objects.create(
            thread=thread, actor="user", event_type="thread_create",
        )
        event.timestamp = timezone.now() - timedelta(days=30)
        event.save(update_fields=["timestamp"])
        before_count = AuditEvent.objects.count()
        deleted = cleanup_old_audit_events(retention_days=90)
        assert deleted == 0
        assert AuditEvent.objects.count() == before_count
