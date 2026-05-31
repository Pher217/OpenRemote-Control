import pytest

from apps.accounts.models import Account
from apps.threads.models import Message, Thread


@pytest.mark.django_db
class TestThreadAPI:
    def test_list_threads(self, authenticated_client):
        response = authenticated_client.get("/api/threads/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_thread(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="t", auth_type="oauth", credential_type="token")
        payload = {"name": "thread-1", "runtime": "claude_code", "runtime_mode": "pty", "account": str(account.id)}
        response = authenticated_client.post("/api/threads/", payload, format="json")
        assert response.status_code == 201
        assert response.data["name"] == "thread-1"

    def test_retrieve_thread(self, authenticated_client):
        account = Account.objects.create(provider="openai", label="t2", auth_type="api_key", credential_type="api_key")
        thread = Thread.objects.create(name="t2", runtime="codex", account=account)
        response = authenticated_client.get(f"/api/threads/{thread.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(thread.id)

    def test_update_thread(self, authenticated_client):
        account = Account.objects.create(provider="ollama", label="t3", auth_type="none", credential_type="none")
        thread = Thread.objects.create(name="t3", runtime="ollama", account=account)
        response = authenticated_client.patch(
            f"/api/threads/{thread.id}/",
            {"status": "running"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == "running"

    def test_delete_thread(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="t4", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="t4", runtime="claude_code", account=account)
        response = authenticated_client.delete(f"/api/threads/{thread.id}/")
        assert response.status_code == 204
        assert Thread.objects.filter(id=thread.id).count() == 0

    def test_list_thread_messages(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="m", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="msg-thread", runtime="claude_code", account=account)
        Message.objects.create(thread=thread, role="user", redacted_content="hello", sequence=1)
        response = authenticated_client.get(f"/api/threads/{thread.id}/messages/")
        assert response.status_code == 200
        assert isinstance(response.data, list)
        assert len(response.data) == 1

    def test_create_thread_message(self, authenticated_client):
        account = Account.objects.create(provider="anthropic", label="m2", auth_type="oauth", credential_type="token")
        thread = Thread.objects.create(name="msg-thread-2", runtime="claude_code", account=account)
        payload = {"role": "assistant", "redacted_content": "hi", "sequence": 1}
        response = authenticated_client.post(f"/api/threads/{thread.id}/messages/", payload, format="json")
        assert response.status_code == 201
        assert response.data["redacted_content"] == "hi"

