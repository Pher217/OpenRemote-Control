"""Atomic, injection-safe updates to ``deploy/.env``.

The wizard collects bot tokens and API keys and must persist them without ever
leaving a half-written env file behind — a truncated ``.env`` takes the whole
stack down on next boot. Every write therefore goes to a sibling temp file and
is swapped in with :func:`os.replace`, which is atomic on POSIX.

Three rules matter more than the mechanics:

* **Keys are allowlisted.** Shape alone is not enough: ``SECRET_KEY``,
  ``POSTGRES_PASSWORD`` and ``ALLOWED_HOSTS`` all match ``^[A-Z][A-Z0-9_]*$``,
  so a shape-only check would hand stack-wide config control to anyone who
  reached a write endpoint. Only :data:`WRITABLE_KEYS` may be written.
* **Values may not contain any line separator Python recognises.** Checking
  just ``\\n``/``\\r`` is not sufficient, because the parser below uses
  ``str.splitlines()``, which also splits on ``\\x0b``, ``\\x0c``, ``\\x1c``-``\\x1e``,
  ``\\x85``, ``U+2028`` and ``U+2029``. A value carrying ``\\x0b`` would survive a
  naive check, be written as one physical line, and then be laundered into a
  real assignment by the *next* update — a second-order injection. Validation
  therefore uses the same splitter the parser uses.
* **Values are single-quoted on write.** Docker Compose interpolates ``$VAR``
  in unquoted ``.env`` values.

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

#: The only keys the setup wizard is permitted to write. Everything governing
#: the security posture of the stack — SECRET_KEY, POSTGRES_*, ALLOWED_HOSTS,
#: DEBUG, ORC_SETUP_*, and the enrollment/connector/gateway secrets — is
#: deliberately absent and stays operator-only.
WRITABLE_KEYS = frozenset(
    {
        # Telegram
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_FORUM_CHAT_ID",
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "ORC_PROMPT_CHAT_ID",
        # Platform selection + gateway destinations
        "ORC_MESSAGING_PLATFORM",
        "ORC_PROMPT_WHATSAPP",
        "ORC_PROMPT_SLACK",
        "ORC_PROMPT_DISCORD",
        "ORC_PROMPT_SIGNAL",
        "ORC_PROMPT_IMESSAGE",
        # LLM providers
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
    }
)

#: Env files hold credentials — owner read/write only.
ENV_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600


class EnvWriteError(RuntimeError):
    """Raised when an update is rejected or cannot be applied safely."""


def validate_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise EnvWriteError(f"invalid env key: {key!r}")
    if key not in WRITABLE_KEYS:
        raise EnvWriteError(f"env key is not wizard-writable: {key!r}")


def validate_value(value: str) -> None:
    """Reject anything that could become a second line, or break quoting.

    ``splitlines()`` is the authority rather than a hand-listed set of escapes,
    so validation can never drift from what the parser will later do.
    """
    if "\x00" in value:
        raise EnvWriteError("env values may not contain NUL")
    if value.splitlines()[:1] != ([value] if value else []):
        raise EnvWriteError("env values may not contain line separators")
    if "'" in value:
        raise EnvWriteError("env values may not contain single quotes")
    # python-dotenv honours \' and \\ escapes inside single quotes while Compose
    # does not, so a backslash makes the two consumers disagree about the value.
    # None of the allowlisted keys needs one.
    if "\\" in value:
        raise EnvWriteError("env values may not contain backslashes")


@contextmanager
def _locked(path: Path):
    """Hold an exclusive lock on a sidecar lockfile for the duration."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW: a pre-planted symlink at the lock path would otherwise let an
    # attacker with write access to the directory truncate an arbitrary file.
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
        values[key.strip()] = _unquote(value.strip())
    return values


def _quote(value: str) -> str:
    """Single-quote a value so Compose does not interpolate ``$VAR`` in it.

    Values containing a single quote are rejected upstream, so no escaping
    scheme is needed here — and none of the allowlisted keys (tokens, chat
    ids, URLs) legitimately contains one.
    """
    return f"'{value}'"


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


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

    # Inside the container BASE_DIR is /app, so the repo-relative default
    # resolves to /deploy — a directory that does not exist and that the app
    # user could not create anyway. Fail with something actionable rather than
    # a bare PermissionError on mkdir.
    if not path.parent.is_dir():
        raise EnvWriteError(
            f"env directory does not exist: {path.parent}. "
            "Set ORC_SETUP_ENV_FILE to a path inside a writable mounted directory."
        )

    with _locked(path):
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = original.splitlines()
        remaining = dict(updates)
        out: list[str] = []

        # A key may legitimately appear more than once in a hand-edited file.
        # Rewriting only the first occurrence would be silently useless: both
        # Compose and dotenv take the LAST assignment, so a stale duplicate
        # further down would keep winning and the credential would never change.
        # Rewrite the first, drop every later one.
        rewritten: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in remaining:
                    out.append(f"{key}={_quote(remaining.pop(key))}")
                    rewritten.add(key)
                    continue
                if key in rewritten:
                    continue
            out.append(line)

        for key, value in remaining.items():
            out.append(f"{key}={_quote(value)}")

        _atomic_write(path, "\n".join(out) + "\n")

    log.info("setup.env_updated", path=str(path), keys=sorted(updates))


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a same-directory temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), ENV_FILE_MODE)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        # The rename itself is atomic but not durable until the directory entry
        # is flushed — without this a crash can leave neither old nor new file.
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        # Never leave a stray temp file holding secrets behind.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
