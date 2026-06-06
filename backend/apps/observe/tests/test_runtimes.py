import json

import pytest

from apps.observe.runtimes import UnknownRuntimeError, get_runtime_adapter
from apps.observe.runtimes.claude_code import ClaudeCodeAdapter


def test_get_runtime_adapter_claude_code():
    adapter = get_runtime_adapter("claude_code")
    assert adapter.provider == "claude_code"


def test_unknown_runtime_raises():
    with pytest.raises(UnknownRuntimeError):
        get_runtime_adapter("missing_runtime")


def test_claude_code_parse_turn_adds_source_and_taint():
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "u1",
            "sessionId": "S1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            },
        }
    )

    parsed = ClaudeCodeAdapter().parse_turn(line)

    assert parsed["role"] == "assistant"
    assert parsed["text"] == "hello"
    assert parsed["uuid"] == "u1"
    assert parsed["session_id"] == "S1"
    assert parsed["source"] == "claude_code"
    assert parsed["taint"] == "observed"


def test_claude_code_scan_file_meta(tmp_path):
    fixture = tmp_path / "S1.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "u1",
                        "sessionId": "S1",
                        "cwd": "/home/u/openremote-control",
                        "gitBranch": "claude/x",
                        "message": {"role": "user", "content": "hi"},
                    }
                ),
                json.dumps(
                    {
                        "type": "ai-title",
                        "sessionId": "S1",
                        "aiTitle": "Runtime Adapter",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert ClaudeCodeAdapter().scan_file_meta(str(fixture)) == {
        "repo": "openremote-control",
        "branch": "claude/x",
        "title": "Runtime Adapter",
    }
