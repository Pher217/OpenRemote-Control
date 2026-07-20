"""Atomic, injection-safe updates to ``deploy/.env``.

The wizard collects bot tokens and API keys and must persist them without ever
leaving a half-written env file behind — a truncated ``.env`` takes the whole
stack down on next boot. Every write therefore goes to a sibling temp file and
is swapped in with :func:`os.replace`, which is atomic on POSIX.

Two rules matter more than the mechanics:

* **Keys are validated.** Only ``^[A-Z][A-Z0-9_]*$`` is accepted.
* **Values may not contain newlines.** A value carrying ``\\n`` would inject an
  entirely new assignment into the file — the way an attacker who reached the
  wizard would flip ``DEBUG`` or overwrite ``SECRET_KEY``. Rejected outright.

Nothing here logs a value. Ever.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import structlog

try:  # POSIX only; the backend always runs on Linux in Docker.
    import fcntl
except ImportError:  # pragma: no cover - Windows dev shells
    fcntl = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: Env files hold credentials — owner read/write only.
ENV_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600


class EnvWriteError(RuntimeError):
    """Raised when an update is rejected or cannot be applied safely."""


def validate_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise EnvWriteError(f"invalid env key: {key!r}")


def validate_value(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise EnvWriteError("env values may not contain newlines")


@contextmanager
def _locked(path: Path):
    """Hold an exclusive lock on a sidecar lockfile for the duration."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_env(path: Path) -> dict[str, str]:
    """Parse ``path`` into a dict. Missing file yields an empty dict."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Apply ``updates`` to the env file at ``path``, atomically.

    Existing keys are rewritten in place so comments and ordering survive;
    new keys are appended. The file is created if absent.
    """
    for key, value in updates.items():
        validate_key(key)
        validate_value(value)
    if not updates:
        return

    with _locked(path):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = original.splitlines()
        remaining = dict(updates)
        out: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in remaining:
                    out.append(f"{key}={remaining.pop(key)}")
                    continue
            out.append(line)

        for key, value in remaining.items():
            out.append(f"{key}={value}")

        _atomic_write(path, "\n".join(out) + "\n")

    log.info("setup.env_updated", path=str(path), keys=sorted(updates))


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a same-directory temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, ENV_FILE_MODE)
        os.replace(tmp_name, path)
    except Exception:
        # Never leave a stray temp file holding secrets behind.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
