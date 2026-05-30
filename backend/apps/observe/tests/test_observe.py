import json

import pytest
from channels.db import database_sync_to_async

from apps.observe.observer import process_lines
from apps.observe.parser import parse_line
from apps.threads.models import Message, Thread


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
