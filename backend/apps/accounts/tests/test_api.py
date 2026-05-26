import pytest

from apps.accounts.models import Account


@pytest.mark.django_db
class TestAccountAPI:
    def test_list_accounts(self, authenticated_client):
        response = authenticated_client.get("/api/accounts/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_account(self, authenticated_client):
        payload = {
            "provider": "anthropic",
            "label": "test-label",
            "auth_type": "oauth",
            "credential_type": "token",
        }
        response = authenticated_client.post("/api/accounts/", payload, format="json")
        assert response.status_code == 201
        assert response.data["provider"] == "anthropic"
        assert response.data["label"] == "test-label"

    def test_retrieve_account(self, authenticated_client):
        account = Account.objects.create(provider="openai", label="test", auth_type="api_key", credential_type="api_key")
        response = authenticated_client.get(f"/api/accounts/{account.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(account.id)

    def test_update_account(self, authenticated_client):
        account = Account.objects.create(provider="ollama", label="local", auth_type="none", credential_type="none")
        response = authenticated_client.patch(
            f"/api/accounts/{account.id}/",
            {"label": "updated-label"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["label"] == "updated-label"

    def test_delete_account(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="del", auth_type="oauth", credential_type="token")
        response = authenticated_client.delete(f"/api/accounts/{account.id}/")
        assert response.status_code == 204
        assert Account.objects.filter(id=account.id).count() == 0

    def test_unauthenticated_list_returns_403(self, api_client):
        response = api_client.get("/api/accounts/")
        assert response.status_code == 403
