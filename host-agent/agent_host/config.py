"""
config.py — HostConfig dataclass and JSON persistence.

The config file lives at:
  $XDG_CONFIG_HOME/openremote-control/host.json
  (defaults to ~/.config/openremote-control/host.json)

An override path may be injected via the ORC_CONFIG_PATH environment variable —
used by tests so they can write to a temp directory without touching the real
config.  The file is written with mode 0o600 (owner-read/write only).
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass
class HostConfig:
    backend_url: str
    host_id: str
    token: str


def config_path() -> Path:
    """Return the path to the host config file.

    Resolution order:
    1. ORC_CONFIG_PATH env var (test/override)
    2. $XDG_CONFIG_HOME/openremote-control/host.json
    3. ~/.config/openremote-control/host.json
    """
    override = os.environ.get("ORC_CONFIG_PATH")
    if override:
        return Path(override)

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "openremote-control" / "host.json"


def save(cfg: HostConfig) -> None:
    """Write *cfg* to the config file as JSON, creating parent dirs if needed.

    File permissions are set to 0600 (owner read/write only) because the
    file contains the per-host bearer token.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "backend_url": cfg.backend_url,
        "host_id": cfg.host_id,
        "token": cfg.token,
    }
    # Write atomically: temp file → rename is not used here because we need
    # to set the mode before first write to avoid a window where the token
    # is world-readable.  We open with O_CREAT|O_WRONLY|O_TRUNC and mode 0o600
    # via os.open so the file is created with the right permissions from the start.
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        # If write fails, fd may have been closed by fdopen — do not double-close.
        raise


def load() -> HostConfig | None:
    """Load and return a HostConfig from the config file, or None if not found."""
    path = config_path()
    if not path.exists():
        return None
    with path.open() as f:
        data = json.load(f)
    return HostConfig(
        backend_url=data["backend_url"],
        host_id=data["host_id"],
        token=data["token"],
    )
