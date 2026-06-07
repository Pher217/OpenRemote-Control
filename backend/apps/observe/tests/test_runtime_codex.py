import json

from apps.observe.runtimes import get_runtime_adapter
from apps.observe.runtimes.codex import CodexAdapter

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_runtime_adapter_codex():
    adapter = get_runtime_adapter("codex")
    assert adapter.provider == "codex"


# ---------------------------------------------------------------------------
# parse_turn — assistant turn (agent_message)
# ---------------------------------------------------------------------------


def test_parse_turn_agent_message_returns_assistant_role():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:30:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "I'll do the vault/session orientation first.",
                "phase": "main",
                "memory_citation": None,
            },
        }
    )

    parsed = CodexAdapter().parse_turn(line)

    assert parsed is not None
    assert parsed["role"] == "assistant"
    assert parsed["text"] == "I'll do the vault/session orientation first."
    assert parsed["source"] == "codex"
    assert parsed["taint"] == "observed"
    assert parsed["uuid"] is None
    assert parsed["session_id"] is None


# ---------------------------------------------------------------------------
# parse_turn — user turn (user_message)
# ---------------------------------------------------------------------------


def test_parse_turn_user_message_returns_user_role():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:50.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Please review the billing logic.",
                "images": [],
                "local_images": [],
                "text_elements": [],
            },
        }
    )

    parsed = CodexAdapter().parse_turn(line)

    assert parsed is not None
    assert parsed["role"] == "user"
    assert parsed["text"] == "Please review the billing logic."
    assert parsed["source"] == "codex"
    assert parsed["taint"] == "observed"


# ---------------------------------------------------------------------------
# parse_turn — skipped line types
# ---------------------------------------------------------------------------


def test_parse_turn_returns_none_on_session_meta():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:32.084Z",
            "type": "session_meta",
            "payload": {
                "id": "019e9dfb-987d-70a2-822d-64a43d66c889",
                "cwd": "/home/u/my-repo",
                "git": {"branch": "main"},
            },
        }
    )

    assert CodexAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_turn_context():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:40.000Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "abc-123",
                "cwd": "/home/u/my-repo",
                "model": "gpt-5.5",
            },
        }
    )

    assert CodexAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_response_item():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:45.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            },
        }
    )

    assert CodexAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_token_count_event():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:48.000Z",
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {}, "rate_limits": {}},
        }
    )

    assert CodexAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_empty_message():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:50.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "   ",
            },
        }
    )

    assert CodexAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_json_decode_error():
    assert CodexAdapter().parse_turn("not json {{{") is None


def test_parse_turn_returns_none_on_non_object():
    assert CodexAdapter().parse_turn(json.dumps([1, 2, 3])) is None


# ---------------------------------------------------------------------------
# extract_session_meta
# ---------------------------------------------------------------------------


def test_extract_session_meta_from_session_meta_line():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:32.084Z",
            "type": "session_meta",
            "payload": {
                "id": "019e9dfb-987d-70a2-822d-64a43d66c889",
                "cwd": "/home/u/openremote-control",
                "git": {"branch": "claude/universal-aggregator"},
            },
        }
    )

    meta = CodexAdapter().extract_session_meta(line)

    assert meta["session_id"] == "019e9dfb-987d-70a2-822d-64a43d66c889"
    assert meta["repo"] == "openremote-control"
    assert meta["branch"] == "claude/universal-aggregator"


def test_extract_session_meta_cross_platform_basename():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:32.084Z",
            "type": "session_meta",
            "payload": {
                "id": "abc-123",
                "cwd": r"C:\Users\u\dev\my-repo",
                "git": {"branch": "main"},
            },
        }
    )

    meta = CodexAdapter().extract_session_meta(line)
    assert meta["repo"] == "my-repo"


def test_extract_session_meta_from_turn_context_yields_repo():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:40.000Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "abc-123",
                "cwd": "/home/u/my-project",
                "model": "gpt-5.5",
            },
        }
    )

    meta = CodexAdapter().extract_session_meta(line)
    assert meta["repo"] == "my-project"
    assert "session_id" not in meta


def test_extract_session_meta_returns_empty_on_non_meta_line():
    line = json.dumps(
        {
            "timestamp": "2026-06-06T17:29:50.000Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Hello",
            },
        }
    )

    assert CodexAdapter().extract_session_meta(line) == {}


def test_extract_session_meta_returns_empty_on_bad_json():
    assert CodexAdapter().extract_session_meta("not json") == {}


# ---------------------------------------------------------------------------
# scan_file_meta
# ---------------------------------------------------------------------------


def test_scan_file_meta_merges_across_lines(tmp_path):
    fixture = tmp_path / "rollout-2026-06-06T17-29-32-019e9dfb-987d-70a2-822d-64a43d66c889.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-06T17:29:32.084Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "019e9dfb-987d-70a2-822d-64a43d66c889",
                            "cwd": "/home/u/openremote-control",
                            "git": {"branch": "claude/universal-aggregator"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-06T17:29:40.000Z",
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "abc-123",
                            "cwd": "/home/u/openremote-control",
                            "model": "gpt-5.5",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-06T17:29:50.000Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "Please review the billing logic.",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    meta = CodexAdapter().scan_file_meta(str(fixture))

    # session_id is stripped from the file-level summary
    assert "session_id" not in meta
    assert meta["repo"] == "openremote-control"
    assert meta["branch"] == "claude/universal-aggregator"


def test_scan_file_meta_returns_empty_on_missing_file():
    assert CodexAdapter().scan_file_meta("/nonexistent/path/session.jsonl") == {}
