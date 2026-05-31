import pytest

from apps.accounts.models import Account
from apps.audit.models import AuditEvent
from apps.threads.models import Thread


@pytest.mark.django_db
class TestAuditEventAPI:
    def test_list_audit_events(self, authenticated_client):
        response = authenticated_client.get("/api/audit/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_retrieve_audit_event(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="au", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="au-thread", runtime="claude_code", account=account)
        event = AuditEvent.objects.create(thread=thread, actor="user1", event_type="thread_create", redacted_payload={"id": str(thread.id)})
        response = authenticated_client.get(f"/api/audit/{event.id}/")
        assert response.status_code == 200
        assert response.data["id"] == event.id

    def test_create_audit_event_fails(self, authenticated_client):
        response = authenticated_client.post("/api/audit/", {}, format="json")
        assert response.status_code == 405
