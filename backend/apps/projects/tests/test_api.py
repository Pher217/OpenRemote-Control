import pytest

from apps.policies.models import PolicyProfile
from apps.projects.models import Project


@pytest.mark.django_db
class TestProjectAPI:
    def test_list_projects(self, authenticated_client):
        response = authenticated_client.get("/api/projects/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_project(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="default")
        payload = {"slug": "proj1", "name": "Project 1", "policy": str(policy.id), "sensitivity": "internal"}
        response = authenticated_client.post("/api/projects/", payload, format="json")
        assert response.status_code == 201
        assert response.data["slug"] == "proj1"

    def test_retrieve_project(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p2")
        project = Project.objects.create(slug="p2", name="P2", policy=policy)
        response = authenticated_client.get(f"/api/projects/{project.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(project.id)

    def test_update_project(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p3")
        project = Project.objects.create(slug="p3", name="P3", policy=policy)
        response = authenticated_client.patch(
            f"/api/projects/{project.id}/",
            {"name": "Updated P3"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["name"] == "Updated P3"

    def test_delete_project(self, authenticated_client):
        policy = PolicyProfile.objects.create(name="p4")
        project = Project.objects.create(slug="p4", name="P4", policy=policy)
        response = authenticated_client.delete(f"/api/projects/{project.id}/")
        assert response.status_code == 204
        assert Project.objects.filter(id=project.id).count() == 0
