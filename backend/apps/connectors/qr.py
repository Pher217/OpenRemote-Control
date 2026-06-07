"""QR-code utilities for connector pairing (UC0).

Uses `segno` to generate both terminal (ASCII) and PNG output.
The pairing payload is a URI-style string the orc-mcp client understands:

    orc-pair://<host>/<code>

where <host> is derived from backend_url (scheme+host stripped).
If backend_url is empty the payload degrades to just the code so the
client still has enough to complete the pairing manually.
"""

from __future__ import annotations

import io
from urllib.parse import urlparse

import segno


def pairing_payload(code: str, backend_url: str) -> str:
    """Build the compact string encoded in the QR code."""
    if backend_url:
        parsed = urlparse(backend_url)
        host = parsed.netloc or parsed.path  # handle bare hostnames too
        return f"orc-pair://{host}/{code}"
    return code


def terminal_qr(data: str) -> str:
    """Return an ANSI/ASCII QR suitable for printing to a terminal."""
    qr = segno.make(data, error="M")
    buf = io.StringIO()
    qr.terminal(out=buf, compact=True)
    return buf.getvalue()


def png_bytes(data: str, scale: int = 5) -> bytes:
    """Return PNG bytes of the QR code at the given pixel scale."""
    qr = segno.make(data, error="M")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale)
    return buf.getvalue()
