"""Tests for orc_mcp.codex_discovery.find_codex_session.

Scans a sessions dir for rollout-*.jsonl files, reads the first line of each as
a ``session_meta`` head record whose ``payload`` carries {source, cwd, id},
filters out ``source == "exec"`` (our own drive forks) and matches the rollout's
``cwd`` against ``realpath(cwd)``, returning the newest-by-mtime matching
session id (or None).
"""
from __future__ import annotations

import json
import os

from orc_mcp.codex_discovery import find_codex_session


def _write_rollout(dirp, name, *, source, cwd, sid, mtime=None):
    """Write a rollout-*.jsonl whose first line is a session_meta head record."""
    import time  # noqa: F401  (kept for parity with the documented helper)

    p = dirp / name
    p.write_text(
        json.dumps(
            {
                "timestamp": "t",
                "type": "session_meta",
                "payload": {"source": source, "cwd": cwd, "id": sid},
            }
        )
        + "\n"
    )
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_finds_matching_vscode_session(tmp_path):
    """
    GIVEN a sessions dir containing a vscode rollout for a project cwd
    WHEN find_codex_session is called with that cwd
    THEN it returns the rollout's session id.
    """
    base = tmp_path / "sessions"
    sub = base / "2024/01/01"
    sub.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_rollout(sub, "rollout-abc.jsonl", source="vscode", cwd=str(proj), sid="sess-A")

    assert find_codex_session(str(proj), sessions_dir=str(base)) == "sess-A"


def test_excludes_exec_sessions(tmp_path):
    """
    GIVEN two rollouts for the same cwd — an exec one (newer) and a vscode one
    (older)
    WHEN find_codex_session is called
    THEN it returns the vscode session id, excluding the exec session even
    though it is newer.
    """
    base = tmp_path / "sessions"
    base.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_rollout(
        base, "rollout-real.jsonl", source="vscode", cwd=str(proj), sid="real", mtime=1000
    )
    _write_rollout(
        base, "rollout-drive.jsonl", source="exec", cwd=str(proj), sid="drive", mtime=2000
    )

    assert find_codex_session(str(proj), sessions_dir=str(base), max_age_s=10**12) == "real"


def test_newest_vscode_wins(tmp_path):
    """
    GIVEN two vscode rollouts for the same cwd with different mtimes
    WHEN find_codex_session is called
    THEN it returns the id of the newest (largest mtime) rollout.
    """
    base = tmp_path / "sessions"
    base.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_rollout(base, "rollout-old.jsonl", source="vscode", cwd=str(proj), sid="old", mtime=1000)
    _write_rollout(base, "rollout-new.jsonl", source="vscode", cwd=str(proj), sid="new", mtime=2000)

    assert find_codex_session(str(proj), sessions_dir=str(base), max_age_s=10**12) == "new"


def test_cwd_mismatch_returns_none(tmp_path):
    """
    GIVEN a vscode rollout whose cwd does not match the requested cwd
    WHEN find_codex_session is called
    THEN it returns None.
    """
    base = tmp_path / "sessions"
    base.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_rollout(
        base, "rollout-other.jsonl", source="vscode", cwd="/somewhere/else", sid="sess-X"
    )

    assert find_codex_session(str(proj), sessions_dir=str(base)) is None


def test_no_files_returns_none(tmp_path):
    """
    GIVEN an empty sessions dir
    WHEN find_codex_session is called
    THEN it returns None.
    """
    base = tmp_path / "sessions"
    base.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()

    assert find_codex_session(str(proj), sessions_dir=str(base)) is None