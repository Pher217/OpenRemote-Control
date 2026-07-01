"""Pure HTTP client for the OpenRemote-Control connector API.

No `mcp` import here so it is unit-testable without the MCP SDK. The long wait for
a human answer is done by POLLING the backend (POST to create a Prompt, then GET
the result), not by holding one long HTTP read — so a dropped connection or a
restarted backend doesn't strand the agent.

Auth strategy (checked once at construction):
  1. If ~/.config/openremote-control/connector_key exists → Ed25519 request signing.
  2. Else if ORC_CONNECTOR_TOKEN is set → legacy Bearer header.
  3. Else → no auth header (backend will 401; surfaced to caller).
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Short per-request timeout; the long wait is the poll loop, not a single read.
_POST_TIMEOUT = 15.0
_POLL_HTTP_TIMEOUT = 15.0


def _default_connector_id() -> str:
    return os.environ.get("ORC_CONNECTOR_ID") or f"orc-{socket.gethostname()}"


class OrcBackendClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        connector_id: str | None = None,
        tool: str | None = None,
        *,
        poll_interval: float = 2.0,
        overall_timeout: float = 600.0,
        transport: httpx.BaseTransport | None = None,
        # Injected in tests to override identity loading
        _signing_identity: tuple["Ed25519PrivateKey", dict] | None | bool = False,
    ):
        self.base_url = (
            base_url or os.environ.get("ORC_BACKEND_URL") or "http://localhost:8000"
        ).rstrip("/")
        self.token = token if token is not None else os.environ.get("ORC_CONNECTOR_TOKEN", "")
        self.tool = tool or os.environ.get("ORC_TOOL", "unknown")
        self.poll_interval = poll_interval
        self.overall_timeout = overall_timeout
        self._transport = transport

        # Resolve signing identity: False = not yet loaded; None = no identity.
        if _signing_identity is False:
            from orc_mcp.signing import load_or_create_identity
            loaded = load_or_create_identity()
        else:
            loaded = _signing_identity  # type: ignore[assignment]

        if loaded is not None:
            self._sign_priv, meta = loaded
            self._sign_connector_id: str = meta["connector_id"]
            self._sign_key_id: str = meta["key_id"]
            # connector_id arg may still override for display; identity's id is authoritative
            self.connector_id = self._sign_connector_id
        else:
            self._sign_priv = None
            self._sign_connector_id = ""
            self._sign_key_id = ""
            self.connector_id = connector_id or _default_connector_id()

    # -- internals ---------------------------------------------------------

    def _identity(self) -> dict:
        return {
            "connector_id": self.connector_id,
            "tool": self.tool,
            "workspace_root": os.getcwd(),
        }

    def _auth_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        """Return auth headers for one request.

        Signing takes priority over bearer token.
        """
        if self._sign_priv is not None:
            from orc_mcp.signing import sign_headers
            return sign_headers(
                self._sign_priv,
                self._sign_connector_id,
                self._sign_key_id,
                method,
                path,
                body,
            )
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _post(self, path: str, payload: dict, timeout: float) -> httpx.Response:
        body = json.dumps({**self._identity(), **payload}, separators=(",", ":")).encode()
        headers = self._auth_headers("POST", path, body)
        with httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        ) as c:
            return c.post(path, content=body, headers={**headers, "Content-Type": "application/json"})

    def _get(self, path: str, timeout: float) -> httpx.Response:
        headers = self._auth_headers("GET", path, b"")
        with httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        ) as c:
            return c.get(path, headers=headers)

    def _create(self, path: str, payload: dict) -> str:
        r = self._post(path, payload, _POST_TIMEOUT)
        r.raise_for_status()
        return r.json()["nonce"]

    def _poll(self, nonce: str) -> dict:
        deadline = time.monotonic() + self.overall_timeout
        while True:
            r = self._get(f"/api/connectors/result/{nonce}", _POLL_HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "pending":
                return data
            if time.monotonic() >= deadline:
                return {"status": "timeout"}
            time.sleep(self.poll_interval)

    # -- public tools ------------------------------------------------------

    def start_remote_control(
        self, name: str = "", claude_session_id: str = "", workspace_root: str = ""
    ) -> str:
        """Start a remote-control session and dispatch it to the operator's chat.

        ``claude_session_id`` binds the driveable chat to the caller's own coding
        session so Telegram replies resume THIS conversation; ``workspace_root`` is
        the cwd the resumed ``claude -p`` runs in. Returns the session name on
        success, or a '[…]' sentinel on failure.
        """
        try:
            body: dict[str, str] = {"name": name}
            if claude_session_id:
                body["claude_session_id"] = claude_session_id
            if workspace_root:
                body["workspace_root"] = workspace_root
            r = self._post("/api/connectors/start", body, _POST_TIMEOUT)
            r.raise_for_status()
            return r.json().get("name") or name or "session"
        except Exception:
            return "[connector error]"

    def notify(self, message: str) -> bool:
        """Fire-and-forget progress to the user's chat. Best-effort."""
        try:
            r = self._post("/api/connectors/notify", {"message": message}, _POST_TIMEOUT)
            r.raise_for_status()
            return bool(r.json().get("ok"))
        except Exception:
            return False

    def ask(self, question: str, options: list[str] | None = None) -> str:
        """Ask the user a question; block (via polling) until answered or timeout."""
        try:
            nonce = self._create(
                "/api/connectors/ask",
                {"question": question, "options": list(options or [])},
            )
            data = self._poll(nonce)
        except Exception:
            return "[connector error]"
        status = data.get("status")
        if status == "answered":
            return data.get("answer") or ""
        if status in ("expired", "cancelled"):
            return "[expired]"
        return "[no answer: timeout]"

    def approve(self, action: str, preview: str = "") -> str:
        """Request approval for a control action. FAIL-CLOSED: 'deny' on timeout/error."""
        try:
            nonce = self._create(
                "/api/connectors/approve",
                {"action": action, "preview": preview},
            )
            data = self._poll(nonce)
        except Exception:
            return "deny"
        if data.get("status") == "answered":
            decision = data.get("decision")
            return decision if decision in ("allow", "deny") else "deny"
        return "deny"
