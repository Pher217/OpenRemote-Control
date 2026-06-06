"""Pure cryptographic helpers for host-daemon authentication.

No Django imports here — this module can be unit-tested without a database.
Nonce-replay protection lives in the consumer (cache.add), not here.
"""

import hashlib
import hmac


def sign(secret: str, host_id: str, ts: str, nonce: str) -> str:
    """Return the HMAC-SHA256 hexdigest over ``{host_id}:{ts}:{nonce}``.

    *secret* is the raw (unhashed) per-host token.
    """
    msg = f"{host_id}:{ts}:{nonce}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_sig(
    secret: str,
    host_id: str,
    ts: str,
    nonce: str,
    signature: str,
    *,
    now: float,
    skew: int = 300,
) -> tuple[bool, str]:
    """Validate *signature* and timestamp freshness.

    Returns ``(True, "")`` on success or ``(False, reason)`` on failure.

    Signature verification uses constant-time comparison. Timestamp check uses
    integer arithmetic so there is no float drift on the boundary.

    The caller is responsible for nonce-replay prevention (``cache.add``).
    """
    try:
        ts_int = int(ts)
    except (ValueError, TypeError):
        return False, "invalid_ts"

    if abs(now - ts_int) > skew:
        return False, "ts_expired"

    expected = sign(secret, host_id, ts, nonce)
    if not hmac.compare_digest(expected, signature):
        return False, "bad_signature"

    return True, ""
