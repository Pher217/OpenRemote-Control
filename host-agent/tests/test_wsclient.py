"""
Tests for wsclient.py — connect_url and run_sender.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest

import agent_host.wsclient as wsclient
from agent_host.config import HostConfig
from agent_host.queue import OfflineQueue
from agent_host.signing import sign
from agent_host.wsclient import connect_url, handle_host_command, run_sender

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


def _cm_connect(ws_per_attempt, *, stop_event=None, stop_at_attempt=1, urls=None):
    """Fake ``connect``: returns an async context manager per call.

    ``async with connect(url) as ws`` is how run_sender opens ONE connection per
    signed URL (mirroring ``websockets.connect`` used as a context manager).

    ws_per_attempt: ws object(s) to yield on successive connect() calls; the last
        entry repeats for further attempts.
    stop_event: set on entering a connection once the (1-based) attempt index
        reaches stop_at_attempt — so run_sender exits cleanly.
    urls: optional list that records each URL passed to connect().
    """
    state = {"n": 0}

    def connect(url):
        idx = state["n"]
        state["n"] += 1
        if urls is not None:
            urls.append(url)
        ws = ws_per_attempt[idx] if idx < len(ws_per_attempt) else ws_per_attempt[-1]

        @asynccontextmanager
        async def _cm():
            if stop_event is not None and (idx + 1) >= stop_at_attempt:
                stop_event.set()
            yield ws

        return _cm()

    return connect


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

    await run_sender(
        cfg, queue, connect=_cm_connect([fake_ws], stop_event=stop), stop=stop
    )

    raw_values = [ev["data"]["raw"] for ev in fake_ws.sent]
    assert "line1" in raw_values
    assert "line2" in raw_values
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

    async def _push_and_stop() -> None:
        while not hasattr(cfg, "_incoming_queue"):
            await asyncio.sleep(0.01)
        event = {"type": "session.line", "data": {"raw": "live-line"}}
        await cfg._incoming_queue.put(event)  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(
        run_sender(cfg, queue, connect=_cm_connect([fake_ws]), stop=stop),
        _push_and_stop(),
    )

    raw_values = [ev.get("data", {}).get("raw") for ev in fake_ws.sent]
    assert "live-line" in raw_values


# ---------------------------------------------------------------------------
# Bug-fix tests: oversized event drain + reconnect re-sign
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drain_skips_oversized_event_and_clears_queue(tmp_path):
    """
    GIVEN an offline queue containing an oversized event followed by a normal event
    WHEN run_sender() connects and drains the queue
    THEN the oversized event is dropped (not sent), the normal event after it is sent,
         and the queue is empty (the oversized event is NOT re-queued).
    """
    from agent_host.wsclient import MAX_EVENT_BYTES

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    big_raw = "X" * (MAX_EVENT_BYTES + 1)
    queue.enqueue({"type": "session.line", "data": {"raw": big_raw}})
    queue.enqueue({"type": "session.line", "data": {"raw": "normal-after-oversized"}})

    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    fake_ws = _FakeWs()

    await run_sender(
        cfg, queue, connect=_cm_connect([fake_ws], stop_event=stop), stop=stop
    )

    sent_raws = [ev.get("data", {}).get("raw", "") for ev in fake_ws.sent]
    assert not any(r == big_raw for r in sent_raws), "oversized event must not be sent"
    assert "normal-after-oversized" in sent_raws
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_drain_oversized_only_queue_is_cleared(tmp_path):
    """
    GIVEN an offline queue containing ONLY an oversized event
    WHEN run_sender() drains it
    THEN nothing is sent and the queue ends up empty (not poisoned).
    """
    from agent_host.wsclient import MAX_EVENT_BYTES

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    queue.enqueue({"type": "session.line", "data": {"raw": "Y" * (MAX_EVENT_BYTES + 1)}})

    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    fake_ws = _FakeWs()

    await run_sender(
        cfg, queue, connect=_cm_connect([fake_ws], stop_event=stop), stop=stop
    )

    assert fake_ws.sent == []
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_reconnect_uses_different_nonces(tmp_path):
    """
    GIVEN run_sender() whose first WS connection fails on a live-stream send
    WHEN it reconnects
    THEN the second connect() receives a URL with a DIFFERENT nonce (fresh re-sign),
         so the backend's nonce-replay cache would not reject it.

    The first connection's send() raises (server closed); run_sender re-queues
    the event, the connection closes, and the OUTER loop re-signs a fresh URL for
    the second attempt — proving reconnects do not reuse the nonce.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    received_urls: list[str] = []

    class _FailOnSendWs:
        async def send(self, data: str) -> None:
            raise ConnectionResetError("server closed connection")

        async def recv(self) -> str:
            await asyncio.sleep(9999)
            return ""

    connect = _cm_connect(
        [_FailOnSendWs(), _FakeWs()],
        stop_event=stop,
        stop_at_attempt=2,
        urls=received_urls,
    )

    async def _push_event() -> None:
        while not hasattr(cfg, "_incoming_queue"):
            await asyncio.sleep(0.005)
        await cfg._incoming_queue.put(  # type: ignore[attr-defined]
            {"type": "session.line", "data": {"raw": "trigger"}}
        )

    await asyncio.gather(
        run_sender(cfg, queue, connect=connect, stop=stop),
        _push_event(),
    )

    assert len(received_urls) >= 2, "Expected at least two connection attempts"
    nonces = [parse_qs(urlparse(u).query)["nonce"][0] for u in received_urls[:2]]
    assert nonces[0] != nonces[1], (
        f"Both reconnect attempts used the same nonce ({nonces[0]!r}); "
        "the backend would reject the second attempt with 403."
    )


# ---------------------------------------------------------------------------
# Bidirectional receive loop tests
# ---------------------------------------------------------------------------


class _FrameWs:
    """Fake WebSocket that yields a sequence of frames from recv(), then raises."""

    def __init__(self, frames: list[str], *, close_exc: Exception | None = None) -> None:
        self._frames = list(frames)
        self._close_exc = close_exc or ConnectionResetError("server closed")
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        raise self._close_exc


@pytest.mark.asyncio
async def test_recv_dispatches_host_command_to_handler(tmp_path):
    """
    GIVEN a WebSocket that yields a host_command frame
    WHEN run_sender() is running
    THEN the on_command callback is called with the frame.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()

    received_commands: list[dict] = []

    def capture_command(frame: dict) -> None:
        received_commands.append(frame)

    ping_frame = json.dumps({"type": "host_command", "command": "ping"})
    fake_ws = _FrameWs([ping_frame])  # yields ping then raises ConnectionResetError

    connect = _cm_connect([fake_ws, _FakeWs()], stop_event=stop, stop_at_attempt=2)

    await run_sender(cfg, queue, connect=connect, stop=stop, on_command=capture_command)

    assert len(received_commands) == 1
    assert received_commands[0]["type"] == "host_command"
    assert received_commands[0]["command"] == "ping"


@pytest.mark.asyncio
async def test_recv_malformed_frame_does_not_kill_loop(tmp_path):
    """
    GIVEN a WebSocket that yields a malformed JSON frame followed by a valid frame
    WHEN run_sender() is running
    THEN the malformed frame is silently ignored and the valid frame is dispatched.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()

    received_commands: list[dict] = []

    def capture_command(frame: dict) -> None:
        received_commands.append(frame)

    malformed = "this is not json{{{"
    valid_frame = json.dumps({"type": "host_command", "command": "ping"})
    fake_ws = _FrameWs([malformed, valid_frame])

    connect = _cm_connect([fake_ws, _FakeWs()], stop_event=stop, stop_at_attempt=2)

    await run_sender(cfg, queue, connect=connect, stop=stop, on_command=capture_command)

    # Only the valid frame should be dispatched; malformed is silently dropped.
    assert len(received_commands) == 1
    assert received_commands[0]["command"] == "ping"


@pytest.mark.asyncio
async def test_recv_error_causes_reconnect(tmp_path):
    """
    GIVEN a WebSocket whose recv() raises immediately
    WHEN run_sender() is running
    THEN the connection exits (gather raises), the outer loop reconnects
         with a fresh signed URL (different nonce), and run_sender eventually stops.
    """
    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    received_urls: list[str] = []

    # First WS: recv raises immediately → gather tears down sender too → reconnect.
    # Second WS: stop is set on entry → run_sender exits cleanly.
    fail_ws = _FrameWs([])  # empty frames list → recv raises ConnectionResetError at once
    ok_ws = _FakeWs()

    connect = _cm_connect(
        [fail_ws, ok_ws],
        stop_event=stop,
        stop_at_attempt=2,
        urls=received_urls,
    )

    await run_sender(cfg, queue, connect=connect, stop=stop)

    # Two connection attempts were made (first failed, second stopped cleanly).
    assert len(received_urls) >= 2
    # The two URLs must have different nonces (fresh re-sign on reconnect).
    nonces = [parse_qs(urlparse(u).query)["nonce"][0] for u in received_urls[:2]]
    assert nonces[0] != nonces[1]


# ---------------------------------------------------------------------------
# handle_host_command unit tests
# ---------------------------------------------------------------------------


def test_handle_host_command_ping_logs_and_acks():
    """
    GIVEN a host_command frame with command="ping" and an outbound queue
    WHEN handle_host_command is called
    THEN an ack event is enqueued in the outbound queue.
    """
    q: asyncio.Queue = asyncio.Queue()
    frame = {"type": "host_command", "command": "ping"}
    handle_host_command(frame, incoming_queue=q)
    assert not q.empty()
    ack = q.get_nowait()
    assert ack["type"] == "host_command_ack"
    assert ack["command"] == "ping"


def test_handle_host_command_ping_no_queue_does_not_raise():
    """
    GIVEN a host_command frame with command="ping" and no outbound queue
    WHEN handle_host_command is called
    THEN it returns without error (ack is skipped gracefully).
    """
    frame = {"type": "host_command", "command": "ping"}
    handle_host_command(frame, incoming_queue=None)  # must not raise


def test_handle_host_command_unknown_does_not_raise():
    """
    GIVEN a host_command frame with an unknown command
    WHEN handle_host_command is called
    THEN it returns without error and does not enqueue anything.
    """
    q: asyncio.Queue = asyncio.Queue()
    frame = {"type": "host_command", "command": "unknown_future_command"}
    handle_host_command(frame, incoming_queue=q)
    assert q.empty()


def test_handle_host_command_pty_inject_does_not_raise():
    """
    GIVEN a host_command frame with command="pty.inject" (Phase 4 stub)
    WHEN handle_host_command is called
    THEN it returns without error (logged as not implemented).
    """
    frame = {"type": "host_command", "command": "pty.inject", "keys": "ls\n"}
    handle_host_command(frame)  # must not raise


# ---------------------------------------------------------------------------
# Heartbeat + watchdog durability tests
# ---------------------------------------------------------------------------

class _SilentWs:
    """Fake WebSocket that returns silently-ignored frames quickly."""

    def __init__(self, recv_delay: float = 0.01) -> None:
        self.sent: list[dict] = []
        self._recv_delay = recv_delay

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        await asyncio.sleep(self._recv_delay)
        return json.dumps({"type": "ignored"})


class _PingingWs:
    """Fake WebSocket that returns host_command ping frames periodically."""

    def __init__(self, interval: float = 0.05) -> None:
        self.sent: list[dict] = []
        self._interval = interval

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        await asyncio.sleep(self._interval)
        return json.dumps({"type": "host_command", "command": "ping"})


@pytest.mark.asyncio
async def test_heartbeat_sent_periodically(tmp_path, monkeypatch):
    """
    GIVEN a fake ws and a short monkeypatched HEARTBEAT_INTERVAL
    WHEN run_sender() is running
    THEN at least one {"type":"host_heartbeat"} frame is sent within a bounded wait.
    """
    monkeypatch.setattr(wsclient, "HEARTBEAT_INTERVAL", 0.05)

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    fake_ws = _FakeWs()

    async def _wait_for_heartbeat() -> None:
        for _ in range(200):  # up to ~2 s
            await asyncio.sleep(0.01)
            if any(ev.get("type") == "host_heartbeat" for ev in fake_ws.sent):
                stop.set()
                return
        raise AssertionError("heartbeat not sent within bounded wait")

    await asyncio.gather(
        run_sender(cfg, queue, connect=_cm_connect([fake_ws]), stop=stop),
        _wait_for_heartbeat(),
    )

    assert any(ev.get("type") == "host_heartbeat" for ev in fake_ws.sent)


@pytest.mark.asyncio
async def test_ping_resets_liveness_no_reconnect(tmp_path, monkeypatch):
    """
    GIVEN a daemon receiving periodic ping host_commands
    WHEN the time since last ping never exceeds HEARTBEAT_TIMEOUT
    THEN the connection is NOT torn down by the watchdog (no reconnect).
    """
    monkeypatch.setattr(wsclient, "HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(wsclient, "HEARTBEAT_TIMEOUT", 0.3)

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    urls: list[str] = []

    pinging_ws = _PingingWs(interval=0.05)
    connect = _cm_connect([pinging_ws], urls=urls)

    async def _stop_after() -> None:
        await asyncio.sleep(0.5)
        stop.set()

    await asyncio.gather(
        run_sender(cfg, queue, connect=connect, stop=stop),
        _stop_after(),
    )

    assert len(urls) == 1


@pytest.mark.asyncio
async def test_watchdog_forces_reconnect_on_silence(tmp_path, monkeypatch):
    """
    GIVEN a connection with NO ping frames returned and a short HEARTBEAT_TIMEOUT
    WHEN the watchdog detects silence
    THEN the connection is torn down and the outer loop reconnects.
    """
    monkeypatch.setattr(wsclient, "HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(wsclient, "HEARTBEAT_TIMEOUT", 0.2)

    queue = OfflineQueue(tmp_path / "queue.jsonl")
    cfg = HostConfig(backend_url="http://localhost", host_id="h1", token="tok")
    stop = asyncio.Event()
    urls: list[str] = []

    silent_ws = _SilentWs(recv_delay=0.01)
    connect = _cm_connect(
        [silent_ws, silent_ws],
        stop_event=stop,
        stop_at_attempt=2,
        urls=urls,
    )

    await run_sender(cfg, queue, connect=connect, stop=stop)

    assert len(urls) >= 2
