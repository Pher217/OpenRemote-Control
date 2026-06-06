"""Tests for multi-runtime discovery (iter_runtime_files) and process_lines routing."""
from __future__ import annotations

import json

import pytest
from channels.db import database_sync_to_async

from apps.observe.observer import process_lines
from apps.observe.runtimes import iter_runtime_files
from apps.threads.models import Thread

# ---------------------------------------------------------------------------
# iter_runtime_files — discovery helper
# ---------------------------------------------------------------------------


def test_iter_runtime_files_uses_default_root_when_env_unset(tmp_path, monkeypatch):
    """
    GIVEN a claude_code adapter whose default_root_env is not set
    WHEN iter_runtime_files('claude_code') is called with the default_root
         pointing at a tmp directory containing two .jsonl files
    THEN both files are returned as (path, mtime) pairs
    """
    from apps.observe.runtimes.claude_code import ClaudeCodeAdapter

    # Monkeypatch the adapter's default_root to our tmp dir and clear the env var.
    monkeypatch.setattr(ClaudeCodeAdapter, "default_root", str(tmp_path))
    monkeypatch.delenv(ClaudeCodeAdapter.default_root_env, raising=False)

    # Invalidate the cached instance so our patched class attribute is picked up.
    from apps.observe.runtimes import _INSTANCES
    _INSTANCES.pop("claude_code", None)

    proj = tmp_path / "proj-abc"
    proj.mkdir()
    (proj / "session1.jsonl").write_text("{}\n", encoding="utf-8")
    (proj / "session2.jsonl").write_text("{}\n", encoding="utf-8")

    results = iter_runtime_files("claude_code")
    paths = {r[0] for r in results}
    assert str(proj / "session1.jsonl") in paths
    assert str(proj / "session2.jsonl") in paths
    assert len(results) == 2


def test_iter_runtime_files_env_override_takes_precedence(tmp_path, monkeypatch):
    """
    GIVEN the OBSERVE_CLAUDE_PROJECTS_DIR env var is set to a custom directory
    WHEN iter_runtime_files('claude_code') is called
    THEN it scans the custom directory, not the adapter's default_root
    """
    from apps.observe.runtimes import _INSTANCES
    from apps.observe.runtimes.claude_code import ClaudeCodeAdapter
    _INSTANCES.pop("claude_code", None)

    custom_root = tmp_path / "custom"
    custom_root.mkdir()
    (custom_root / "a.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setenv(ClaudeCodeAdapter.default_root_env, str(custom_root))

    results = iter_runtime_files("claude_code")
    assert len(results) == 1
    assert results[0][0] == str(custom_root / "a.jsonl")


def test_iter_runtime_files_returns_empty_when_root_missing(tmp_path, monkeypatch):
    """
    GIVEN the scan root directory does not exist
    WHEN iter_runtime_files is called
    THEN an empty list is returned (no error raised)
    """
    from apps.observe.runtimes import _INSTANCES
    from apps.observe.runtimes.claude_code import ClaudeCodeAdapter
    _INSTANCES.pop("claude_code", None)

    monkeypatch.setattr(ClaudeCodeAdapter, "default_root", str(tmp_path / "nonexistent"))
    monkeypatch.delenv(ClaudeCodeAdapter.default_root_env, raising=False)

    results = iter_runtime_files("claude_code")
    assert results == []


def test_iter_runtime_files_gemini_uses_nested_chats_glob(tmp_path, monkeypatch):
    """
    GIVEN a gemini adapter whose discovery_glob is '**/chats/*.jsonl'
    WHEN iter_runtime_files('gemini') is called with a directory that has both
         a file directly in root and one inside chats/
    THEN only the chats/ file is returned
    """
    from apps.observe.runtimes import _INSTANCES
    from apps.observe.runtimes.gemini import GeminiAdapter
    _INSTANCES.pop("gemini", None)

    monkeypatch.setattr(GeminiAdapter, "default_root", str(tmp_path))
    monkeypatch.delenv(GeminiAdapter.default_root_env, raising=False)

    proj_hash = tmp_path / "abc123"
    chats_dir = proj_hash / "chats"
    chats_dir.mkdir(parents=True)
    (chats_dir / "session-1.jsonl").write_text("{}\n", encoding="utf-8")
    # This file is in root, not under chats/ — must NOT be discovered.
    (proj_hash / "toplevel.jsonl").write_text("{}\n", encoding="utf-8")

    results = iter_runtime_files("gemini")
    assert len(results) == 1
    assert "chats" in results[0][0]


def test_iter_runtime_files_codex_default_glob(tmp_path, monkeypatch):
    """
    GIVEN a codex adapter with discovery_glob '**/*.jsonl'
    WHEN iter_runtime_files('codex') is called
    THEN all .jsonl files under root are discovered regardless of depth
    """
    from apps.observe.runtimes import _INSTANCES
    from apps.observe.runtimes.codex import CodexAdapter
    _INSTANCES.pop("codex", None)

    monkeypatch.setattr(CodexAdapter, "default_root", str(tmp_path))
    monkeypatch.delenv(CodexAdapter.default_root_env, raising=False)

    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "sess.jsonl").write_text("{}\n", encoding="utf-8")

    results = iter_runtime_files("codex")
    assert len(results) == 1
    assert results[0][0].endswith("sess.jsonl")


# ---------------------------------------------------------------------------
# process_lines — codex provider creates Thread with runtime='codex'
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_process_lines_codex_creates_thread_with_codex_runtime():
    """
    GIVEN a synthetic codex session_meta line followed by a user_message event
    WHEN process_lines is called with provider='codex'
    THEN a Thread with runtime='codex' is created in the database
    """
    session_id = "codex-test-session-001"

    fixture = [
        json.dumps(
            {
                "timestamp": "2026-06-06T10:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": "/home/u/openremote-control",
                    "git": {"branch": "claude/universal-aggregator"},
                },
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-06-06T10:00:01.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "Wire the observer to run multiple runtimes.",
                },
            }
        ),
    ]

    events: list = []

    async def on_turn(thread, p, msg):
        events.append((thread, p, msg))

    await process_lines(
        fixture,
        "/tmp/codex-test-session-001.jsonl",
        on_turn=on_turn,
        provider="codex",
    )

    @database_sync_to_async
    def _get_thread():
        return Thread.objects.filter(runtime="codex").first()

    thread = await _get_thread()
    assert thread is not None, "No Thread with runtime='codex' was created"
    assert thread.runtime == "codex"
    assert thread.runtime_mode == Thread.RuntimeModeChoices.OBSERVED
    assert len(events) == 1
    assert events[0][1]["role"] == "user"
    assert "multiple runtimes" in events[0][1]["text"]
