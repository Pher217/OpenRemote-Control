from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable


class UnknownRuntimeError(Exception):
    """Raised when no observe runtime adapter is registered for a provider."""


@runtime_checkable
class RuntimeAdapter(Protocol):
    provider: str
    # Env-var name whose value overrides the scan root (e.g. "OBSERVE_CLAUDE_PROJECTS_DIR").
    default_root_env: str
    # Absolute default root path used when the env var is unset.
    default_root: str
    # Glob pattern relative to root (e.g. "**/*.jsonl" or "**/chats/*.jsonl").
    discovery_glob: str

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


def iter_runtime_files(provider: str) -> list[tuple[str, float]]:
    """Return (path, mtime) pairs for all JSONL files belonging to a runtime.

    The scan root is resolved in order:
    1. os.environ[adapter.default_root_env]  (per-runtime override)
    2. adapter.default_root                   (sensible default)

    Returns an empty list when the root directory does not exist.
    """
    adapter = get_runtime_adapter(provider)
    configured = os.environ.get(adapter.default_root_env, "")
    root = Path(configured) if configured else Path(adapter.default_root)
    if not root.exists():
        return []
    paths = list(root.glob(adapter.discovery_glob))
    return [(str(p), os.path.getmtime(p)) for p in paths]
