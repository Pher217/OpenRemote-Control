"""Pure HTTP client for the OpenRemote-Control connector API.

No `mcp` import here so it is unit-testable without the MCP SDK. The long wait for
a human answer is done by POLLING the backend (POST to create a Prompt, then GET
the result), not by holding one long HTTP read — so a dropped connection or a
restarted backend doesn't strand the agent.
"""

from __future__ import annotations

import os
import socket
import time

import httpx

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
    ):
        self.base_url = (
            base_url or os.environ.get("ORC_BACKEND_URL") or "http://localhost:8000"
        ).rstrip("/")
        self.token = token if token is not None else os.environ.get("ORC_CONNECTOR_TOKEN", "")
        self.connector_id = connector_id or _default_connector_id()
        self.tool = tool or os.environ.get("ORC_TOOL", "unknown")
        self.poll_interval = poll_interval
        self.overall_timeout = overall_timeout
        self._transport = transport  # injected in tests (httpx.MockTransport)

    # -- internals ---------------------------------------------------------
    def _identity(self) -> dict:
        return {
            "connector_id": self.connector_id,
            "tool": self.tool,
            "workspace_root": os.getcwd(),
        }

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout,
            transport=self._transport,
        )

    def _create(self, path: str, payload: dict) -> str:
        with self._client(_POST_TIMEOUT) as c:
            r = c.post(path, json={**self._identity(), **payload})
            r.raise_for_status()
            return r.json()["nonce"]

    def _poll(self, nonce: str) -> dict:
        deadline = time.monotonic() + self.overall_timeout
        with self._client(_POLL_HTTP_TIMEOUT) as c:
            while True:
                r = c.get(f"/api/connectors/result/{nonce}")
                r.raise_for_status()
                data = r.json()
                if data.get("status") != "pending":
                    return data
                if time.monotonic() >= deadline:
                    return {"status": "timeout"}
                time.sleep(self.poll_interval)

    # -- public tools ------------------------------------------------------
    def notify(self, message: str) -> bool:
        """Fire-and-forget progress to the user's chat. Best-effort."""
        try:
            with self._client(_POST_TIMEOUT) as c:
                r = c.post("/api/connectors/notify", json={**self._identity(), "message": message})
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
