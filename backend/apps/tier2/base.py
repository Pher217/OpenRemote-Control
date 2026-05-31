from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol, runtime_checkable


class UnknownProviderError(Exception):
    """Raised when no Tier 2 adapter is registered for a provider."""


@dataclass
class NormalizedEvent:
    """A provider-agnostic streaming event from a Tier 2 adapter."""

    kind: str  # message_delta | message_complete | tool_call | error | done
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tier2Adapter(Protocol):
    provider: str

    def stream(
        self, thread: Any, history: list[dict[str, Any]]
    ) -> AsyncIterator[NormalizedEvent]:
        """Yield NormalizedEvents for the given thread and message history.

        Implementations are async generators, so this is declared with the
        AsyncIterator return type rather than `async def`.
        """
        ...


_REGISTRY: dict[str, type] = {}


def register_adapter(cls: type) -> type:
    """Class decorator registering an adapter under its `provider` string."""
    _REGISTRY[cls.provider] = cls
    return cls


def get_adapter(provider: str) -> Tier2Adapter:
    """Resolve a provider string to an adapter instance.

    Lazily imports `apps.tier2.<provider>` so adapter modules self-register
    on first use. Raises UnknownProviderError if no adapter is found.
    """
    if provider not in _REGISTRY:
        try:
            import_module(f"apps.tier2.{provider}")
        except ModuleNotFoundError as exc:
            raise UnknownProviderError(provider) from exc
    try:
        return _REGISTRY[provider]()
    except KeyError as exc:
        raise UnknownProviderError(provider) from exc
