import pytest

from apps.hosts.models import Host


@pytest.mark.django_db
class TestHostAPI:
    def test_list_hosts(self, authenticated_client):
        response = authenticated_client.get("/api/hosts/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_create_host(self, authenticated_client):
        payload = {"slug": "host1", "name": "Host 1", "os": "linux", "status": "online"}
        response = authenticated_client.post("/api/hosts/", payload, format="json")
        assert response.status_code == 201
        assert response.data["slug"] == "host1"

    def test_retrieve_host(self, authenticated_client):
        host = Host.objects.create(slug="h2", name="H2", os="darwin", status="offline")
        response = authenticated_client.get(f"/api/hosts/{host.id}/")
        assert response.status_code == 200
        assert response.data["id"] == str(host.id)

    def test_update_host(self, authenticated_client):
        host = Host.objects.create(slug="h3", name="H3", os="win32", status="online")
        response = authenticated_client.patch(
            f"/api/hosts/{host.id}/",
            {"name": "Updated H3"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["name"] == "Updated H3"

    def test_delete_host(self, authenticated_client):
        host = Host.objects.create(slug="h4", name="H4", os="linux", status="degraded")
        response = authenticated_client.delete(f"/api/hosts/{host.id}/")
        assert response.status_code == 204
        assert Host.objects.filter(id=host.id).count() == 0
