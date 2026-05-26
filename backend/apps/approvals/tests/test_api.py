import pytest
from django.utils import timezone

from apps.accounts.models import Account
from apps.approvals.models import ApprovalRequest
from apps.threads.models import Thread


@pytest.mark.django_db
class TestApprovalRequestAPI:
    def test_list_approvals(self, authenticated_client):
        response = authenticated_client.get("/api/approvals/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_approval(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="a", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="appr", runtime="claude_code", account=account)
        payload = {
            "thread": str(thread.id),
            "request_type": "run_command",
            "risk": "high",
            "summary": "rm -rf /",
            "expires_at": timezone.now().isoformat(),
        }
        response = authenticated_client.post("/api/approvals/", payload, format="json")
        assert response.status_code == 201
        assert response.data["summary"] == "rm -rf /"

    def test_retrieve_approval(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="a2", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="appr2", runtime="claude_code", account=account)
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type="push_branch",
            risk="low",
            summary="push",
            expires_at=timezone.now(),
        )
        response = authenticated_client.get(f"/api/approvals/{approval.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(approval.id)

    def test_update_approval_status(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="a3", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="appr3", runtime="claude_code", account=account)
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type="run_command",
            risk="medium",
            summary="cmd",
            expires_at=timezone.now(),
        )
        response = authenticated_client.patch(
            f"/api/approvals/{approval.id}/",
            {"status": "approved"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == "approved"

    def test_delete_approval(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="a4", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="appr4", runtime="claude_code", account=account)
        approval = ApprovalRequest.objects.create(
            thread=thread,
            request_type="deploy",
            risk="high",
            summary="dep",
            expires_at=timezone.now(),
        )
        response = authenticated_client.delete(f"/api/approvals/{approval.id}/")
        assert response.status_code == 204
        assert ApprovalRequest.objects.filter(id=approval.id).count() == 0
