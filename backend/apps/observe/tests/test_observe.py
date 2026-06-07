import json

import pytest
from channels.db import database_sync_to_async

from apps.observe.observer import process_lines, select_session_files
from apps.observe.parser import extract_session_meta, parse_line, scan_file_meta
from apps.observe.service import get_or_create_observed_thread
from apps.threads.models import Message, Thread

NOW = 1_000_000.0


def _info(slug, name, age_seconds):
    return (f"C:/x/projects/{slug}/{name}.jsonl", NOW - age_seconds)


def test_select_no_filters_returns_all():
    infos = [
        _info("c--Users-u-dev-openerp", "a", 30),
        _info("c--Users-u-dev-other", "b", 99999),
    ]
    result = select_session_files(infos, projects=[], active_minutes=0, now_ts=NOW)
    assert result == [infos[0][0], infos[1][0]]


def test_select_projects_substring_case_insensitive():
    infos = [
        _info("c--Users-u-dev-OpenERP", "a", 30),
        _info("c--Users-u-dev-agent-command-center", "b", 30),
        _info("c--Users-u-dev-unrelated", "c", 30),
    ]
    result = select_session_files(
        infos, projects=["openerp", "agent-command"], active_minutes=0, now_ts=NOW
    )
    assert result == [infos[0][0], infos[1][0]]


def test_select_active_minutes_drops_stale():
    infos = [
        _info("c--Users-u-dev-openerp", "fresh", 300),
        _info("c--Users-u-dev-openerp", "stale", 1200),
    ]
    result = select_session_files(infos, projects=[], active_minutes=10, now_ts=NOW)
    assert result == [infos[0][0]]


def test_select_projects_and_recency_combine():
    infos = [
        _info("c--Users-u-dev-openerp", "fresh-match", 60),
        _info("c--Users-u-dev-openerp", "stale-match", 1200),
        _info("c--Users-u-dev-other", "fresh-nomatch", 60),
    ]
    result = select_session_files(
        infos, projects=["openerp"], active_minutes=10, now_ts=NOW
    )
    assert result == [infos[0][0]]


def test_extract_text_only_blocks():
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": "S1",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        }
    )
    assert parse_line(line)["text"] == "hi"


def test_mixed_blocks_keeps_only_text():
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "u2",
            "sessionId": "S1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "Hello there"},
                    {"type": "tool_use", "name": "Read", "input": {}},
                ],
            },
        }
    )
    assert parse_line(line)["text"] == "Hello there"


def test_content_as_plain_string():
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u3",
            "sessionId": "S1",
            "message": {"role": "user", "content": "just a string"},
        }
    )
    assert parse_line(line)["text"] == "just a string"


def test_user_tool_result_only_is_none():
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u4",
            "sessionId": "S1",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "ok"}],
            },
        }
    )
    assert parse_line(line) is None


def test_non_turn_types_are_none():
    assert parse_line(json.dumps({"type": "queue-operation"})) is None
    assert parse_line(json.dumps({"type": "ai-title", "title": "x"})) is None


def test_malformed_json_is_none():
    assert parse_line("{not json") is None


def test_extract_session_meta_from_turn_line():
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": "S1",
            "cwd": "c:\\Users\\u\\dev\\agent-command-center",
            "gitBranch": "claude/2026-05-25-foo",
            "message": {"role": "user", "content": "hi"},
        }
    )
    meta = extract_session_meta(line)
    assert meta["repo"] == "agent-command-center"
    assert meta["branch"] == "claude/2026-05-25-foo"
    assert meta["session_id"] == "S1"
    assert "title" not in meta


def test_extract_session_meta_from_ai_title_line():
    line = json.dumps(
        {
            "type": "ai-title",
            "sessionId": "S1",
            "aiTitle": "Execute Tier 2 vertical slice",
        }
    )
    meta = extract_session_meta(line)
    assert meta["title"] == "Execute Tier 2 vertical slice"
    assert meta["session_id"] == "S1"


def test_extract_session_meta_noise_and_bad_json():
    assert extract_session_meta("{not json") == {}
    assert extract_session_meta(json.dumps([1, 2])) == {}
    assert extract_session_meta(json.dumps({"type": "queue-operation"})) == {}


def test_scan_file_meta_merges(tmp_path):
    fixture = tmp_path / "S1.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "u1",
                        "sessionId": "S1",
                        "cwd": "/home/u/agent-command-center",
                        "gitBranch": "claude/x",
                        "message": {"role": "user", "content": "hi"},
                    }
                ),
                json.dumps(
                    {
                        "type": "ai-title",
                        "sessionId": "S1",
                        "aiTitle": "My Title",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    meta = scan_file_meta(str(fixture))
    assert meta == {
        "repo": "agent-command-center",
        "branch": "claude/x",
        "title": "My Title",
    }


def test_scan_file_meta_missing_file():
    assert scan_file_meta("/no/such/file.jsonl") == {}


@pytest.mark.django_db(transaction=True)
def test_create_thread_sets_meta_and_name(tmp_path):
    fixture = tmp_path / "S9.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "uuid": "a1",
                        "sessionId": "S9",
                        "cwd": "/home/u/agent-command-center",
                        "gitBranch": "claude/y",
                        "message": {"role": "assistant", "content": "hi"},
                    }
                ),
                json.dumps(
                    {"type": "ai-title", "sessionId": "S9", "aiTitle": "Session Title"}
                ),
            ]
        ),
        encoding="utf-8",
    )
    thread = get_or_create_observed_thread("S9", str(fixture))
    assert thread.name == "Session Title"
    assert thread.metadata["repo"] == "agent-command-center"
    assert thread.metadata["branch"] == "claude/y"
    assert thread.metadata["title"] == "Session Title"
    assert thread.metadata["provider"] == "claude_code"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_process_lines_updates_title_from_ai_title():
    fixture = [
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "S2",
                "cwd": "/home/u/agent-command-center",
                "gitBranch": "claude/z",
                "message": {"role": "user", "content": "hi"},
            }
        ),
        json.dumps({"type": "ai-title", "sessionId": "S2", "aiTitle": "Live Title"}),
    ]

    async def on_turn(thread, p, msg):
        pass

    await process_lines(fixture, "/tmp/S2.jsonl", on_turn=on_turn)

    @database_sync_to_async
    def _thread():
        return Thread.objects.get(external_session_ref="S2")

    thread = await _thread()
    assert thread.metadata["title"] == "Live Title"
    assert thread.name == "Live Title"
    assert thread.metadata["repo"] == "agent-command-center"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_process_lines_persists_and_dedups():
    fixture = [
        json.dumps({"type": "queue-operation", "uuid": "q0"}),
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "S1",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Question?"}],
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "uuid": "u2",
                "sessionId": "S1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "..."},
                        {"type": "text", "text": "Hello there"},
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "uuid": "u3",
                "sessionId": "S1",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "ok"}],
                },
            }
        ),
    ]

    events = []

    async def on_turn(thread, p, msg):
        events.append((thread, p, msg))

    seen = await process_lines(fixture, "/tmp/S1.jsonl", on_turn=on_turn)

    @database_sync_to_async
    def _threads():
        return list(Thread.objects.all())

    @database_sync_to_async
    def _messages(thread):
        return list(
            Message.objects.filter(thread=thread).order_by("sequence")
        )

    threads = await _threads()
    assert len(threads) == 1
    thread = threads[0]
    assert thread.external_session_ref == "S1"
    assert thread.runtime_mode == Thread.RuntimeModeChoices.OBSERVED

    messages = await _messages(thread)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].redacted_content == "Question?"
    assert messages[1].role == "assistant"
    assert messages[1].redacted_content == "Hello there"
    assert len(events) == 2

    await process_lines(fixture, "/tmp/S1.jsonl", on_turn=on_turn, seen=seen)
    messages_after = await _messages(thread)
    assert len(messages_after) == 2
