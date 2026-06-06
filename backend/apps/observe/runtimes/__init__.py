from __future__ import annotations

from importlib import import_module
from typing import Protocol, runtime_checkable


class UnknownRuntimeError(Exception):
    """Raised when no observe runtime adapter is registered for a provider."""


@runtime_checkable
class RuntimeAdapter(Protocol):
    provider: str

    def parse_turn(self, raw: str) -> dict | None:
        ...

    def extract_session_meta(self, raw: str) -> dict:
        ...

    def scan_file_meta(self, path: str) -> dict:
        ...


_REGISTRY: dict[str, type] = {}
_INSTANCES: dict[str, RuntimeAdapter] = {}


def register_runtime_adapter(cls: type) -> type:
    """Class decorator registering a runtime adapter under its `provider` string."""
    _REGISTRY[cls.provider] = cls
    _INSTANCES.pop(cls.provider, None)
    return cls


def get_runtime_adapter(provider: str) -> RuntimeAdapter:
    """Resolve a runtime provider string to a cached adapter instance.

    Lazily imports `apps.observe.runtimes.<provider>` so adapter modules
    self-register on first use. Raises UnknownRuntimeError if no adapter is found.
    """
    if provider in _INSTANCES:
        return _INSTANCES[provider]
    if provider not in _REGISTRY:
        try:
            import_module(f"apps.observe.runtimes.{provider}")
        except ModuleNotFoundError as exc:
            raise UnknownRuntimeError(provider) from exc
    try:
        adapter = _REGISTRY[provider]()
    except KeyError as exc:
        raise UnknownRuntimeError(provider) from exc
    _INSTANCES[provider] = adapter
    return adapter
