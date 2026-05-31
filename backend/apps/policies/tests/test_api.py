import pytest

from apps.policies.models import PolicyProfile


@pytest.mark.django_db
class TestPolicyProfileAPI:
    def test_list_policies(self, authenticated_client):
        response = authenticated_client.get("/api/policies/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_policy(self, authenticated_client):
        payload = {"name": "strict", "sensitivity_max": "regulated", "block_destructive": True}
        response = authenticated_client.post("/api/policies/", payload, format="json")
        assert response.status_code == 201
        assert response.data["name"] == "strict"

    def test_retrieve_policy(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p2")
        response = authenticated_client.get(f"/api/policies/{policy.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(policy.id)

    def test_update_policy(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p3")
        response = authenticated_client.patch(
            f"/api/policies/{policy.id}/",
            {"name": "updated-p3"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["name"] == "updated-p3"

    def test_delete_policy(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p4")
        response = authenticated_client.delete(f"/api/policies/{policy.id}/")
        assert response.status_code == 204
        assert PolicyProfile.objects.filter(id=policy.id).count() == 0
