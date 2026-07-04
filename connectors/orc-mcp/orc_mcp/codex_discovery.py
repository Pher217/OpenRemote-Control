"""codex_discovery.py — find the operator's current Codex session to bind to.

Codex does NOT expose its session id to the MCP subprocess (verified: no env
marker). But each Codex session writes a rollout at
``~/.codex/sessions/YYYY/MM/DD/rollout-*-<uuid>.jsonl`` whose first line is a
``session_meta`` record carrying the session ``id``, the workspace ``cwd``, and
a ``source`` ("vscode" for a Codex Desktop session, "exec" for a headless
``codex exec`` run — i.e. our OWN drive engine).

``codex exec resume`` FORKS a session (it copies history into a new rollout;
the original is untouched), so binding is a context-snapshot handoff, not a
live two-way mirror. To pick the operator's real interactive session — and
never our own drive forks — discovery:

- matches the rollout's ``cwd`` to ``realpath(cwd)`` (Codex records realpaths,
  so /tmp vs /private/tmp must be normalised on both sides),
- excludes ``source == "exec"`` (our drive sessions pollute the same tree),
- takes the newest by mtime within a recent window.

The result is captured ONCE at dispatch and pinned by the caller — never
re-discovered per turn (a re-discovery would find our own drive fork).
"""

from __future__ import annotations

import glob
import json
import os
import time


def find_codex_session(
    cwd: str,
    *,
    sessions_dir: str | None = None,
    max_age_s: int = 48 * 3600,
) -> str | None:
    """Return the id of the operator's current interactive Codex session in cwd.

    Returns None when no confident match exists (caller then starts fresh and
    should say so — see the honest-fallback UX requirement).
    """
    if not cwd:
        return None
    base = sessions_dir or os.path.expanduser("~/.codex/sessions")
    try:
        target = os.path.realpath(cwd)
    except OSError:
        return None
    now = time.time()
    best: tuple[float, str] | None = None
    for path in glob.glob(os.path.join(base, "**", "rollout-*.jsonl"), recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime > max_age_s:
            continue  # stale — cheap filter before reading the file
        try:
            with open(path, encoding="utf-8") as fh:
                head = json.loads(fh.readline())
        except (OSError, ValueError):
            continue
        payload = head.get("payload") or {}
        if payload.get("source") == "exec":
            continue  # our own `codex exec` drive sessions — never bind these
        rec_cwd = payload.get("cwd")
        if not rec_cwd:
            continue
        try:
            if os.path.realpath(rec_cwd) != target:
                continue
        except OSError:
            continue
        sid = payload.get("id")
        if not sid:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, sid)
    return best[1] if best else None
