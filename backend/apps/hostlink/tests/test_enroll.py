import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.hostlink.models import HostToken
from apps.hosts.models import Host


@pytest.fixture
def client():
    return APIClient()


ENROLL_URL = "/api/hostlink/enroll"
VALID_SECRET = "super-secret-enroll-key"
VALID_PAYLOAD = {
    "enroll_secret": VALID_SECRET,
    "hostname": "my-dev-box",
    "os": "linux",
    "hw_uuid": "hw-uuid-abc-123",
}


@pytest.mark.django_db
class TestEnrollView:
    @override_settings(ORC_ENROLL_SECRET="")
    def test_503_when_secret_unset(self, client):
        """
        GIVEN ORC_ENROLL_SECRET is empty
        WHEN POST /api/hostlink/enroll is called
        THEN it returns 503
        """
        response = client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        assert response.status_code == 503

    @override_settings(ORC_ENROLL_SECRET=VALID_SECRET)
    def test_401_when_wrong_secret(self, client):
        """
        GIVEN ORC_ENROLL_SECRET is set
        WHEN POST /api/hostlink/enroll is called with a wrong secret
        THEN it returns 401
        """
        payload = {**VALID_PAYLOAD, "enroll_secret": "wrong-secret"}
        response = client.post(ENROLL_URL, payload, format="json")
        assert response.status_code == 401

    @override_settings(ORC_ENROLL_SECRET=VALID_SECRET)
    def test_200_creates_host_and_returns_token(self, client):
        """
        GIVEN ORC_ENROLL_SECRET is set and the correct secret is provided
        WHEN POST /api/hostlink/enroll is called
        THEN it returns 200 with host_id, host_slug, and token
        """
        response = client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        assert response.status_code == 200
        data = response.json()
        assert "host_id" in data
        assert "host_slug" in data
        assert "token" in data
        assert len(data["token"]) > 10

    @override_settings(ORC_ENROLL_SECRET=VALID_SECRET)
    def test_creates_host_in_db(self, client):
        """
        GIVEN ORC_ENROLL_SECRET is set
        WHEN POST /api/hostlink/enroll is called with a new hostname
        THEN a Host is created in the database
        """
        client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        assert Host.objects.filter(name="my-dev-box").exists()

    @override_settings(ORC_ENROLL_SECRET=VALID_SECRET)
    def test_idempotent_for_same_hw_uuid(self, client):
        """
        GIVEN the same hw_uuid is enrolled twice
        WHEN POST /api/hostlink/enroll is called twice
        THEN only one Host is created
        """
        client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        assert Host.objects.filter(capabilities__hw_uuid="hw-uuid-abc-123").count() == 1

    @override_settings(ORC_ENROLL_SECRET=VALID_SECRET)
    def test_token_verifies(self, client):
        """
        GIVEN a successful enroll
        WHEN the returned token is passed to HostToken.verify()
        THEN it returns True
        """
        response = client.post(ENROLL_URL, VALID_PAYLOAD, format="json")
        data = response.json()
        host = Host.objects.get(id=data["host_id"])
        assert HostToken.verify(host, data["token"]) is True
