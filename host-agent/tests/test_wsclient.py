"""
Tests for wsclient.py — connect_url and run_sender.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest

from agent_host.config import HostConfig
from agent_host.queue import OfflineQueue
from agent_host.signing import sign
from agent_host.wsclient import connect_url, run_sender

# ---------------------------------------------------------------------------
# connect_url tests
# ---------------------------------------------------------------------------

class TestConnectUrl:
    def _cfg(self, backend_url: str = "https://orc.example.com") -> HostConfig:
        return HostConfig(
            backend_url=backend_url,
            host_id="host-uuid-001",
            token="my-token",
        )

    def test_https_becomes_wss(self):
        """
        GIVEN a backend_url with https scheme
        WHEN connect_url() is called
        THEN the result has wss scheme.
        """
        url = connect_url("https://orc.example.com", self._cfg())
        assert url.startswith("wss://")

    def test_http_becomes_ws(self):
        """
        GIVEN a backend_url with http scheme
        WHEN connect_url() is called
        THEN the result has ws scheme.
        """
        url = connect_url("http://localhost:8000", self._cfg("http://localhost:8000"))
        assert url.startswith("ws://")

    def test_url_contains_host_id_path(self):
        """URL path must include /ws/hosts/{host_id}/."""
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        assert f"/ws/hosts/{cfg.host_id}/" in url

    def test_url_has_token_param(self):
        """URL query must include token parameter."""
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert "token" in params
        assert params["token"][0] == cfg.token

    def test_url_has_ts_param(self):
        """URL query must include ts (unix timestamp) parameter."""
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert "ts" in params
        ts = params["ts"][0]
        assert ts.isdigit()
        assert int(ts) > 1_700_000_000  # sanity: after 2023

    def test_url_has_nonce_param(self):
        """URL query must include nonce parameter."""
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert "nonce" in params
        assert len(params["nonce"][0]) > 0

    def test_url_has_valid_signature(self):
        """
        GIVEN the URL query parameters
        WHEN the signature is recomputed using sign()
        THEN it matches the signature in the URL.
        """
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        ts = params["ts"][0]
        nonce = params["nonce"][0]
        sig_in_url = params["signature"][0]

        expected_sig = sign(cfg.token, cfg.host_id, ts, nonce)
        assert sig_in_url == expected_sig

    def test_signature_matches_hmac_formula_directly(self):
        """
        Verify the signature against the raw HMAC formula (not via sign()) to
        catch any divergence between connect_url and the backend contract.
        """
        cfg = self._cfg()
        url = connect_url(cfg.backend_url, cfg)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        ts = params["ts"][0]
        nonce = params["nonce"][0]
        sig_in_url = params["signature"][0]

        message = f"{cfg.host_id}:{ts}:{nonce}".encode()
        key = cfg.token.encode("utf-8")
        expected = hmac.new(key, message, hashlib.sha256).hexdigest()
        assert sig_in_url == expected

    def test_two_calls_produce_different_nonces(self):
        """Fresh ts+nonce on every call — URLs must not be replayable."""
        cfg = self._cfg()
        url1 = connect_url(cfg.backend_url, cfg)
        url2 = connect_url(cfg.backend_url, cfg)
        # The nonces (and likely ts) differ.
        p1 = parse_qs(urlparse(url1).query)
        p2 = parse_qs(urlparse(url2).query)
        assert p1["nonce"][0] != p2["nonce"][0]


# ---------------------------------------------------------------------------
# run_sender tests (async, using a fake WebSocket)
# ---------------------------------------------------------------------------

class _FakeWs:
    """Minimal fake WebSocket that records sent messages and does not error."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        # Block forever (daemon only sends, never reads in these tests).
        await asyncio.sleep(9999)
        return ""


@pytest.mark.asyncio
async def test_run_sender_drains_queued_events(tmp_path):
    """
    GIVEN events in the offline queue
    WHEN run_sender() connects
    THEN it sends all queued events and they are removed from the queue.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    queue.enqueue({"type": "session.line", "data": {"raw": "line1"}})
    queue.enqueue({"type": "session.line", "data": {"raw": "line2"}})

    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    fake_ws = _FakeWs()
    stop = asyncio.Event()

    @asynccontextmanager
    async def _fake_connect(url: str) -> AsyncIterator[_FakeWs]:  # type: ignore
        yield fake_ws

    class _FakeConnectIter:
        """Mimics `async for ws in connect(url)` — yields once then stops."""

        def __init__(self, url: str) -> None:
            self._url = url
            self._yielded = False

        def __aiter__(self) -> _FakeConnectIter:
            return self

        async def __anext__(self) -> _FakeWs:
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            stop.set()  # Stop after first connection so the test doesn't hang.
            return fake_ws

    def fake_connect(url: str) -> _FakeConnectIter:
        return _FakeConnectIter(url)

    await run_sender(cfg, queue, connect=fake_connect, stop=stop)

    # Both queued events must have been sent.
    raw_values = [ev["data"]["raw"] for ev in fake_ws.sent]
    assert "line1" in raw_values
    assert "line2" in raw_values

    # Queue should be empty after successful drain.
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_run_sender_sends_incoming_events(tmp_path):
    """
    GIVEN a new event pushed to cfg._incoming_queue after connection
    WHEN run_sender() is running
    THEN the event is sent over the WebSocket.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    fake_ws = _FakeWs()

    class _FakeConnectIter:
        def __init__(self, url: str) -> None:
            self._url = url
            self._yielded = False

        def __aiter__(self) -> _FakeConnectIter:
            return self

        async def __anext__(self) -> _FakeWs:
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            return fake_ws

    def fake_connect(url: str) -> _FakeConnectIter:
        return _FakeConnectIter(url)

    async def _push_and_stop() -> None:
        # Wait until the sender has attached the incoming queue.
        while not hasattr(cfg, "_incoming_queue"):
            await asyncio.sleep(0.01)
        event = {"type": "session.line", "data": {"raw": "live-line"}}
        await cfg._incoming_queue.put(event)  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(
        run_sender(cfg, queue, connect=fake_connect, stop=stop),
        _push_and_stop(),
    )

    raw_values = [ev.get("data", {}).get("raw") for ev in fake_ws.sent]
    assert "live-line" in raw_values
