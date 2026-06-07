"""
enroll.py — One-shot enrollment against the backend hostlink endpoint.

Calls POST {backend_url}/api/hostlink/enroll with the enroll secret and
host metadata.  On success, builds a HostConfig, persists it, and returns it.

The *http* parameter accepts an httpx.Client (or anything with a .post()
method) so that tests can inject a MockTransport without opening real sockets.
"""

from __future__ import annotations

import platform
import socket
import uuid
from typing import Any

import httpx

from agent_host.config import HostConfig, save


def _stable_hw_uuid() -> str:
    """Return a stable hardware-derived UUID hex string.

    Uses uuid.getnode() (the machine's MAC address as an integer) and formats
    it as a compact hex string.  This is stable across reboots on the same
    machine as long as the primary network interface does not change.
    """
    node = uuid.getnode()
    return format(node, "012x")


def enroll(
    backend_url: str,
    enroll_secret: str,
    *,
    hostname: str | None = None,
    os_name: str | None = None,
    hw_uuid: str | None = None,
    http: Any = None,
) -> HostConfig:
    """Enroll this host with the backend and return the saved HostConfig.

    Parameters
    ----------
    backend_url:
        Base URL of the backend, e.g. "https://orc.example.com".
        Must NOT have a trailing slash.
    enroll_secret:
        Shared secret configured on the backend.
    hostname:
        Defaults to socket.gethostname().
    os_name:
        Defaults to platform.system().lower() (e.g. "darwin", "linux").
    hw_uuid:
        Defaults to the MAC-address-derived hex string from uuid.getnode().
    http:
        Optional pre-configured httpx.Client.  When None, a default client
        with a 30-second timeout is used.

    Returns
    -------
    HostConfig
        The persisted config containing backend_url, host_id, and token.

    Raises
    ------
    PermissionError
        HTTP 401 — wrong enroll secret.
    RuntimeError
        HTTP 503 — backend enroll secret not configured.
    RuntimeError
        Any other non-200 status code.
    httpx.HTTPError
        Network or transport errors.
    """
    url = f"{backend_url.rstrip('/')}/api/hostlink/enroll"

    payload = {
        "enroll_secret": enroll_secret,
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "os": os_name if os_name is not None else platform.system().lower(),
        "hw_uuid": hw_uuid if hw_uuid is not None else _stable_hw_uuid(),
    }

    client_provided = http is not None
    client = http if client_provided else httpx.Client(timeout=30.0)

    try:
        response = client.post(url, json=payload)
    finally:
        if not client_provided:
            client.close()

    if response.status_code == 200:
        body = response.json()
        cfg = HostConfig(
            backend_url=backend_url.rstrip("/"),
            host_id=body["host_id"],
            token=body["token"],
        )
        save(cfg)
        return cfg

    if response.status_code == 401:
        raise PermissionError("Enrollment rejected: wrong enroll secret (HTTP 401)")

    if response.status_code == 503:
        raise RuntimeError("Enrollment failed: backend enroll secret not configured (HTTP 503)")

    raise RuntimeError(
        f"Enrollment failed: unexpected HTTP {response.status_code}: {response.text[:200]}"
    )
