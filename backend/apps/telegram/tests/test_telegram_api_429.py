"""send_message respects Telegram 429 rate-limiting (retry_after)."""

import pytest

from apps.telegram import telegram_api


class _Resp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        r = self._responses[self.calls]
        self.calls += 1
        return r


@pytest.mark.asyncio
async def test_send_message_retries_once_on_429(monkeypatch):
    """
    GIVEN Telegram returns 429 with retry_after, then 200
    WHEN send_message is called
    THEN it sleeps retry_after and retries once, returning the message_id.
    """
    client = _FakeClient([
        _Resp(429, {"ok": False, "parameters": {"retry_after": 3}}),
        _Resp(200, {"ok": True, "result": {"message_id": 42}}),
    ])
    monkeypatch.setattr(telegram_api.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(telegram_api, "_base_url", lambda: "https://api.telegram.org/botX")

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(telegram_api.asyncio, "sleep", fake_sleep)

    mid = await telegram_api.send_message(-100, "hi")

    assert mid == 42
    assert client.calls == 2  # retried exactly once
    assert slept == [3.0]  # respected retry_after


@pytest.mark.asyncio
async def test_send_message_caps_retry_after(monkeypatch):
    """
    GIVEN a 429 with an absurd retry_after
    WHEN send_message retries
    THEN the wait is capped (never blocks the drainer indefinitely).
    """
    client = _FakeClient([
        _Resp(429, {"ok": False, "parameters": {"retry_after": 9999}}),
        _Resp(200, {"ok": True, "result": {"message_id": 7}}),
    ])
    monkeypatch.setattr(telegram_api.httpx, "AsyncClient", lambda *a, **k: client)
    monkeypatch.setattr(telegram_api, "_base_url", lambda: "https://api.telegram.org/botX")

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(telegram_api.asyncio, "sleep", fake_sleep)

    mid = await telegram_api.send_message(-100, "hi")
    assert mid == 7
    assert slept and slept[0] <= telegram_api._MAX_RETRY_AFTER
