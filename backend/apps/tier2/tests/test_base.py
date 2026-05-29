from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from apps.tier2.base import (
    NormalizedEvent,
    Tier2Adapter,
    UnknownProviderError,
    get_adapter,
    register_adapter,
)


def test_normalized_event_default_payload_is_independent():
    a = NormalizedEvent(kind="done")
    b = NormalizedEvent(kind="done")
    assert a.payload == {}
    assert b.payload == {}
    a.payload["x"] = 1
    assert b.payload == {}


@register_adapter
class FakeAdapter:
    provider = "fake_test"

    async def stream(
        self, thread: Any, history: list[dict[str, Any]]
    ) -> AsyncIterator[NormalizedEvent]:
        yield NormalizedEvent(kind="message_delta", payload={"text": "hi"})


def test_get_adapter_returns_registered_instance():
    adapter = get_adapter("fake_test")
    assert isinstance(adapter, FakeAdapter)
    assert isinstance(adapter, Tier2Adapter)


@pytest.mark.asyncio
async def test_fake_adapter_stream_yields_expected_event():
    adapter = get_adapter("fake_test")
    events = [event async for event in adapter.stream(thread=None, history=[])]
    assert events == [NormalizedEvent(kind="message_delta", payload={"text": "hi"})]


def test_get_adapter_unknown_provider_raises():
    with pytest.raises(UnknownProviderError):
        get_adapter("definitely_not_a_provider")
