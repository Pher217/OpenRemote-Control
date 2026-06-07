"""Ed25519 identity and request signing for the ORC connector.

Each connector install has a unique keypair. The private key lives at
~/.config/openremote-control/connector_key (PEM), and metadata alongside it in
connector.json. Every backend request carries five X-ORC-* headers whose
signature covers: METHOD, PATH, sha256hex(body), timestamp, nonce.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def _config_dir() -> Path:
    return Path.home() / ".config" / "openremote-control"


def _key_file() -> Path:
    return _config_dir() / "connector_key"


def _meta_file() -> Path:
    return _config_dir() / "connector.json"


# ---------------------------------------------------------------------------
# Key generation and persistence
# ---------------------------------------------------------------------------


def generate_keypair() -> Ed25519PrivateKey:
    """Return a fresh Ed25519 private key."""
    return Ed25519PrivateKey.generate()


def save_identity(
    priv: Ed25519PrivateKey,
    connector_id: str,
    key_id: str,
    backend_url: str,
) -> None:
    """Persist the private key + metadata to ~/.config/openremote-control/."""
    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    key_path = _key_file()
    pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    key_path.write_bytes(pem)
    key_path.chmod(0o600)

    _meta_file().write_text(
        json.dumps(
            {"connector_id": connector_id, "key_id": key_id, "backend_url": backend_url},
            indent=2,
        )
    )


def load_or_create_identity() -> tuple[Ed25519PrivateKey, dict] | None:
    """Return (private_key, meta_dict) if an identity is stored, else None.

    Does NOT auto-create one — call pair.pair() to provision a new identity.
    """
    key_path = _key_file()
    meta_path = _meta_file()
    if not key_path.exists() or not meta_path.exists():
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = key_path.read_bytes()
        priv = load_pem_private_key(pem, password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            return None
        meta = json.loads(meta_path.read_text())
        return priv, meta
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public key encoding
# ---------------------------------------------------------------------------


def public_key_b64(priv: Ed25519PrivateKey) -> str:
    """Standard (non-URL-safe) base64 of the raw 32-byte Ed25519 public key."""
    raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


# ---------------------------------------------------------------------------
# Canonical message and signing
# ---------------------------------------------------------------------------


def _canonical(method: str, path: str, body: bytes, ts: str, nonce: str) -> bytes:
    sha = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, sha, ts, nonce]).encode("utf-8")


def sign_headers(
    priv: Ed25519PrivateKey,
    connector_id: str,
    key_id: str,
    method: str,
    path: str,
    body: bytes,
) -> dict[str, str]:
    """Return the five X-ORC-* headers for one request.

    The canonical message (signed bytes) is exactly:
        METHOD\\nPATH\\nsha256hex(body)\\nTIMESTAMP\\nNONCE
    encoded to UTF-8, matching the backend's verification logic.
    """
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    msg = _canonical(method, path, body, ts, nonce)
    sig_raw = priv.sign(msg)
    sig_b64 = base64.b64encode(sig_raw).decode()
    return {
        "X-ORC-Connector-Id": connector_id,
        "X-ORC-Key-Id": key_id,
        "X-ORC-Timestamp": ts,
        "X-ORC-Nonce": nonce,
        "X-ORC-Signature": sig_b64,
    }
