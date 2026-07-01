"""transcript_paths.py — locate a provider session transcript on disk."""
from __future__ import annotations

import glob
import os
import re


def claude_transcript_path(cwd: str, session_id: str) -> str | None:
    """Return the path to the Claude Code JSONL transcript for (cwd, session_id).

    Claude Code stores transcripts at ~/.claude/projects/<enc>/<session_id>.jsonl
    where <enc> is the cwd with every non-alphanumeric character replaced by '-'.
    Primary: compute that path directly. Fallback: if it does not exist, glob
    ~/.claude/projects/*/<session_id>.jsonl (session ids are unique uuids, so a
    single match is authoritative). Returns None when nothing is found.
    """
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    base = os.path.expanduser("~/.claude/projects")
    primary = os.path.join(base, enc, session_id + ".jsonl")
    if os.path.isfile(primary):
        return primary

    matches = glob.glob(os.path.join(base, "*", session_id + ".jsonl"))
    if len(matches) == 1:
        return matches[0]
    return None
