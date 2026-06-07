"""
tailer.py — Efficient tail-from-offset reading and persistent offset store.

read_new_lines()
    Reads a file from a byte offset, returns only complete lines (those
    followed by a newline) and the new offset.  Partial last lines are
    excluded so that we never send a half-written JSON record.

OffsetStore
    Persists per-file byte offsets to a JSON file so the daemon survives
    restarts without re-sending already-sent lines.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def read_new_lines(path: str, offset: int) -> tuple[list[str], int]:
    """Read new complete lines from *path* starting at byte *offset*.

    Parameters
    ----------
    path:
        Absolute path to the JSONL file.
    offset:
        Byte offset to start reading from (0 for the beginning).

    Returns
    -------
    (lines, new_offset)
        *lines* is a list of raw line strings **including** the trailing
        newline character.  Only complete lines (ending with ``\\n``) are
        returned; a partial last line is left for the next call.
        *new_offset* is the byte position of the end of the last complete line
        returned, or *offset* unchanged if no complete lines were found.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return [], offset

    if not chunk:
        return [], offset

    # Find the last complete newline boundary.
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        # No complete line in the new data yet.
        return [], offset

    complete = chunk[: last_nl + 1]
    lines = complete.decode("utf-8", errors="replace").splitlines(keepends=True)
    new_offset = offset + last_nl + 1
    return lines, new_offset


class OffsetStore:
    """Persist per-file byte offsets to a JSON file.

    The store is lazy-loaded: the file is read on first access and written
    on every ``set()`` call.  The file is created with mode 0o600.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            base = Path(xdg) if xdg else Path.home() / ".config"
            path = base / "openremote-control" / "offsets.json"
        self._path = Path(path)
        self._data: dict[str, int] | None = None

    def _load(self) -> None:
        if self._data is not None:
            return
        if self._path.exists():
            try:
                with self._path.open() as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def get(self, path: str) -> int:
        """Return the stored offset for *path*, or 0 if not stored."""
        self._load()
        assert self._data is not None
        return self._data.get(path, 0)

    def set(self, path: str, offset: int) -> None:
        """Store *offset* for *path* and persist immediately."""
        self._load()
        assert self._data is not None
        self._data[path] = offset
        self.save()

    def save(self) -> None:
        """Write the current offsets to disk atomically."""
        self._load()
        assert self._data is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp = self._path.with_suffix(".tmp")
        fd = os.open(
            tmp,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            raise

        os.replace(tmp, self._path)
