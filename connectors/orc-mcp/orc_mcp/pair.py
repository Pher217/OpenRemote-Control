"""One-time pairing: claim a code from the backend to register this connector's public key."""

from __future__ import annotations

import os
import socket

import httpx

from orc_mcp.signing import generate_keypair, public_key_b64, save_identity


def pair(code: str, backend_url: str, *, _transport: httpx.BaseTransport | None = None) -> dict:
    """Claim a pairing code and register this connector with the backend.

    POSTs to {backend_url}/api/connectors/pair/claim with:
        {code, tool, public_key, label}
    On 200, saves the Ed25519 identity locally and returns:
        {"connector_id": ..., "key_id": ...}

    Raises RuntimeError with a clear message on any non-200 response.
    """
    priv = generate_keypair()
    pub_b64 = public_key_b64(priv)
    tool = os.environ.get("ORC_TOOL", "unknown")
    label = socket.gethostname()

    url = backend_url.rstrip("/") + "/api/connectors/pair/claim"
    payload = {"code": code, "tool": tool, "public_key": pub_b64, "label": label}

    kw: dict = {"timeout": 15.0}
    if _transport is not None:
        kw["transport"] = _transport

    with httpx.Client(**kw) as c:
        r = c.post(url, json=payload)

    if r.status_code != 200:
        raise RuntimeError(
            f"Pairing failed: HTTP {r.status_code} — {r.text[:200]}"
        )

    data = r.json()
    connector_id = data["connector_id"]
    key_id = data["key_id"]

    save_identity(priv, connector_id, key_id, backend_url)

    return {"connector_id": connector_id, "key_id": key_id}
