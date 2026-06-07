"""
Tests for enroll.py — enrollment against the backend hostlink endpoint.

All tests use httpx MockTransport — no real network connections are made.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_host.config import load
from agent_host.enroll import enroll


def _mock_transport(status: int, body: dict | None = None) -> httpx.MockTransport:
    """Return an httpx.MockTransport that always responds with *status* and *body*."""
    content = json.dumps(body or {}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


@pytest.fixture()
def tmp_config_env(tmp_path, monkeypatch):
    """Redirect config persistence to a temp directory."""
    cfg_file = tmp_path / "host.json"
    monkeypatch.setenv("ORC_CONFIG_PATH", str(cfg_file))
    return tmp_path


class TestEnrollSuccess:
    def test_200_returns_host_config(self, tmp_config_env):
        """
        GIVEN the backend returns 200 with host_id, host_slug, token
        WHEN enroll() is called
        THEN it returns a HostConfig with the correct values.
        """
        transport = _mock_transport(200, {
            "host_id": "h-uuid-001",
            "host_slug": "my-mac",
            "token": "tok-xyz",
        })
        client = httpx.Client(transport=transport)

        cfg = enroll(
            "https://orc.example.com",
            "my-secret",
            hostname="testhost",
            http=client,
        )

        assert cfg.host_id == "h-uuid-001"
        assert cfg.token == "tok-xyz"
        assert cfg.backend_url == "https://orc.example.com"

    def test_200_saves_config_to_disk(self, tmp_config_env):
        """
        GIVEN a 200 response
        WHEN enroll() is called
        THEN the config is persisted and load() returns the same values.
        """
        transport = _mock_transport(200, {
            "host_id": "h-uuid-002",
            "host_slug": "my-mac",
            "token": "tok-saved",
        })
        client = httpx.Client(transport=transport)
        enroll("https://orc.example.com", "secret", http=client)

        loaded = load()
        assert loaded is not None
        assert loaded.host_id == "h-uuid-002"
        assert loaded.token == "tok-saved"

    def test_request_body_contains_enroll_secret(self, tmp_config_env):
        """
        GIVEN enroll() is called with an enroll_secret
        WHEN the request is sent
        THEN the request body includes the enroll_secret.
        """
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, content=json.dumps({
                "host_id": "h-001",
                "host_slug": "slug",
                "token": "tok",
            }).encode())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        enroll("https://orc.example.com", "my-enroll-secret", http=client)

        assert len(captured) == 1
        body = json.loads(captured[0].content)
        assert body["enroll_secret"] == "my-enroll-secret"

    def test_request_body_contains_hostname(self, tmp_config_env):
        """Request body must include the hostname field."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, content=json.dumps({
                "host_id": "h-001",
                "host_slug": "slug",
                "token": "tok",
            }).encode())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        enroll("https://orc.example.com", "secret", hostname="custom-host", http=client)

        body = json.loads(captured[0].content)
        assert body["hostname"] == "custom-host"

    def test_request_url_is_correct(self, tmp_config_env):
        """POST must go to {backend}/api/hostlink/enroll."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, content=json.dumps({
                "host_id": "h", "host_slug": "s", "token": "t",
            }).encode())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        enroll("https://orc.example.com", "secret", http=client)

        assert str(captured[0].url) == "https://orc.example.com/api/hostlink/enroll"

    def test_trailing_slash_stripped_from_backend_url(self, tmp_config_env):
        """Backend URL trailing slash must not create double slashes."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, content=json.dumps({
                "host_id": "h", "host_slug": "s", "token": "t",
            }).encode())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        enroll("https://orc.example.com/", "secret", http=client)

        assert str(captured[0].url) == "https://orc.example.com/api/hostlink/enroll"


class TestEnrollErrors:
    def test_401_raises_permission_error(self, tmp_config_env):
        """
        GIVEN the backend returns 401
        WHEN enroll() is called
        THEN PermissionError is raised.
        """
        transport = _mock_transport(401)
        client = httpx.Client(transport=transport)

        with pytest.raises(PermissionError, match="401"):
            enroll("https://orc.example.com", "wrong-secret", http=client)

    def test_503_raises_runtime_error(self, tmp_config_env):
        """
        GIVEN the backend returns 503
        WHEN enroll() is called
        THEN RuntimeError is raised mentioning 503.
        """
        transport = _mock_transport(503)
        client = httpx.Client(transport=transport)

        with pytest.raises(RuntimeError, match="503"):
            enroll("https://orc.example.com", "secret", http=client)

    def test_other_error_status_raises_runtime_error(self, tmp_config_env):
        """Any non-200/401/503 status raises RuntimeError."""
        transport = _mock_transport(500, {"detail": "server crashed"})
        client = httpx.Client(transport=transport)

        with pytest.raises(RuntimeError, match="500"):
            enroll("https://orc.example.com", "secret", http=client)
