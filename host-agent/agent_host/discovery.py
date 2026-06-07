"""
discovery.py — Locate JSONL session-transcript files for each AI runtime.

RUNTIME_ROOTS maps a provider name to a (root_dir, glob_pattern) tuple.
The root_dir may reference env vars (resolved at call time) or ~ (expanded).

Providers:
  claude_code  — default: $OBSERVE_CLAUDE_PROJECTS_DIR or ~/.claude/projects
  codex        — ~/.codex/sessions
  gemini       — ~/.gemini/tmp
"""

from __future__ import annotations

import os
from pathlib import Path

# (root_dir_fn, glob_pattern)
# root_dir_fn is a zero-arg callable so that env-var resolution happens at
# call time, not at import time (important for tests that patch env vars).
RUNTIME_ROOTS: dict[str, tuple[str, str]] = {
    "claude_code": (
        os.environ.get("OBSERVE_CLAUDE_PROJECTS_DIR", "~/.claude/projects"),
        "**/*.jsonl",
    ),
    "codex": (
        "~/.codex/sessions",
        "**/*.jsonl",
    ),
    "gemini": (
        "~/.gemini/tmp",
        "**/chats/*.jsonl",
    ),
}


def _resolve_root(provider: str) -> Path | None:
    """Return the expanded root Path for *provider*, or None if not in map."""
    entry = RUNTIME_ROOTS.get(provider)
    if entry is None:
        return None

    raw_root, _ = entry

    # Re-read env var for claude_code so tests can patch it after import.
    if provider == "claude_code":
        raw_root = os.environ.get("OBSERVE_CLAUDE_PROJECTS_DIR", "~/.claude/projects")

    return Path(raw_root).expanduser()


def iter_files(provider: str) -> list[str]:
    """Return a sorted list of absolute JSONL paths for *provider*.

    Returns an empty list if the root directory does not exist or *provider*
    is not in RUNTIME_ROOTS.
    """
    entry = RUNTIME_ROOTS.get(provider)
    if entry is None:
        return []

    _, pattern = entry
    root = _resolve_root(provider)
    assert root is not None  # guaranteed by the entry check above

    if not root.exists():
        return []

    return sorted(str(p.resolve()) for p in root.glob(pattern) if p.is_file())
