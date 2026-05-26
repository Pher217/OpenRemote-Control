import pytest

from apps.skills.models import Skill


@pytest.mark.django_db
class TestSkillAPI:
    def test_list_skills(self, authenticated_client):
        response = authenticated_client.get("/api/skills/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_skill(self, authenticated_client):
        payload = {"name": "code-review", "description": "Review code", "default_runtime": "claude_code"}
        response = authenticated_client.post("/api/skills/", payload, format="json")
        assert response.status_code == 201
        assert response.data["name"] == "code-review"

    def test_retrieve_skill(self, authenticated_client):
        skill = Skill.objects.create(name="refactor")
        response = authenticated_client.get(f"/api/skills/{skill.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(skill.id)

    def test_update_skill(self, authenticated_client):
        skill = Skill.objects.create(name="test-skill")
        response = authenticated_client.patch(
            f"/api/skills/{skill.id}/",
            {"name": "updated-skill"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["name"] == "updated-skill"

    def test_delete_skill(self, authenticated_client):
        skill = Skill.objects.create(name="del-skill")
        response = authenticated_client.delete(f"/api/skills/{skill.id}/")
        assert response.status_code == 204
        assert Skill.objects.filter(id=skill.id).count() == 0
