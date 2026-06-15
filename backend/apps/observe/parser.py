"""Compatibility shim for the active runtime transcript adapter.

Delegates line/turn parsing and session metadata extraction to the concrete
adapter so the rest of the observe app can stay runtime-agnostic.
"""
from apps.observe.runtimes.claude_code import ClaudeCodeAdapter

_ADAPTER = ClaudeCodeAdapter()


def extract_text(content) -> str:
    return _ADAPTER.extract_text(content)


def parse_line(raw: str) -> dict | None:
    parsed = _ADAPTER.parse_turn(raw)
    if parsed is None:
        return None
    return {
        "role": parsed["role"],
        "text": parsed["text"],
        "uuid": parsed["uuid"],
        "session_id": parsed["session_id"],
    }


def extract_session_meta(raw: str) -> dict:
    return _ADAPTER.extract_session_meta(raw)


def scan_file_meta(path) -> dict:
    return _ADAPTER.scan_file_meta(path)
