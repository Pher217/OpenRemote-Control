import json

from apps.observe.runtimes import get_runtime_adapter
from apps.observe.runtimes.gemini import GeminiAdapter

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_runtime_adapter_gemini():
    adapter = get_runtime_adapter("gemini")
    assert adapter.provider == "gemini"


# ---------------------------------------------------------------------------
# parse_turn — role mapping
# ---------------------------------------------------------------------------


def test_parse_turn_user_maps_to_user():
    line = json.dumps(
        {
            "type": "user",
            "session_id": "S1",
            "uuid": "u1",
            "content": {"text": "What is the weather?"},
        }
    )

    parsed = GeminiAdapter().parse_turn(line)

    assert parsed is not None
    assert parsed["role"] == "user"
    assert parsed["text"] == "What is the weather?"
    assert parsed["uuid"] == "u1"
    assert parsed["session_id"] == "S1"
    assert parsed["source"] == "gemini"
    assert parsed["taint"] == "observed"


def test_parse_turn_gemini_maps_to_assistant():
    line = json.dumps(
        {
            "type": "gemini",
            "session_id": "S1",
            "uuid": "u2",
            "content": {"text": "It is sunny."},
        }
    )

    parsed = GeminiAdapter().parse_turn(line)

    assert parsed is not None
    assert parsed["role"] == "assistant"
    assert parsed["text"] == "It is sunny."
    assert parsed["source"] == "gemini"
    assert parsed["taint"] == "observed"


# ---------------------------------------------------------------------------
# parse_turn — parts-style content (Gemini REST shape)
# ---------------------------------------------------------------------------


def test_parse_turn_parts_style_content():
    line = json.dumps(
        {
            "type": "gemini",
            "session_id": "S2",
            "content": {"parts": [{"text": "Hello"}, {"text": "world"}]},
        }
    )

    parsed = GeminiAdapter().parse_turn(line)

    assert parsed is not None
    assert parsed["role"] == "assistant"
    assert parsed["text"] == "Hello\nworld"


# ---------------------------------------------------------------------------
# parse_turn — skipped record types
# ---------------------------------------------------------------------------


def test_parse_turn_returns_none_on_session_metadata():
    line = json.dumps(
        {
            "type": "session_metadata",
            "session_id": "S1",
            "cwd": "/home/u/my-project",
        }
    )

    assert GeminiAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_message_update():
    line = json.dumps({"type": "message_update", "delta": "partial text"})

    assert GeminiAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_empty_text():
    line = json.dumps(
        {
            "type": "user",
            "session_id": "S1",
            "content": {"text": "   "},
        }
    )

    assert GeminiAdapter().parse_turn(line) is None


def test_parse_turn_returns_none_on_json_decode_error():
    assert GeminiAdapter().parse_turn("not json {{{") is None


def test_parse_turn_returns_none_on_non_object():
    assert GeminiAdapter().parse_turn(json.dumps([1, 2, 3])) is None


# ---------------------------------------------------------------------------
# extract_session_meta
# ---------------------------------------------------------------------------


def test_extract_session_meta_from_session_metadata_line():
    line = json.dumps(
        {
            "type": "session_metadata",
            "session_id": "S1",
            "cwd": "/home/u/openremote-control",
            "gitBranch": "claude/universal-aggregator",
            "title": "Runtime Adapter Work",
        }
    )

    meta = GeminiAdapter().extract_session_meta(line)

    assert meta["session_id"] == "S1"
    assert meta["repo"] == "openremote-control"
    assert meta["branch"] == "claude/universal-aggregator"
    assert meta["title"] == "Runtime Adapter Work"


def test_extract_session_meta_cross_platform_basename():
    line = json.dumps(
        {
            "type": "session_metadata",
            "session_id": "S2",
            "cwd": r"C:\Users\u\dev\my-repo",
        }
    )

    meta = GeminiAdapter().extract_session_meta(line)
    assert meta["repo"] == "my-repo"


def test_extract_session_meta_returns_empty_on_bad_json():
    assert GeminiAdapter().extract_session_meta("not json") == {}


# ---------------------------------------------------------------------------
# scan_file_meta
# ---------------------------------------------------------------------------


def test_scan_file_meta_merges_across_lines(tmp_path):
    fixture = tmp_path / "session-abc.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_metadata",
                        "session_id": "S1",
                        "cwd": "/home/u/openremote-control",
                        "gitBranch": "claude/universal-aggregator",
                    }
                ),
                json.dumps(
                    {
                        "type": "session_metadata",
                        "session_id": "S1",
                        "title": "Gemini Adapter",
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "session_id": "S1",
                        "content": {"text": "hi"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    meta = GeminiAdapter().scan_file_meta(str(fixture))

    # session_id is stripped from the file-level summary
    assert "session_id" not in meta
    assert meta["repo"] == "openremote-control"
    assert meta["branch"] == "claude/universal-aggregator"
    assert meta["title"] == "Gemini Adapter"


def test_scan_file_meta_returns_empty_on_missing_file():
    assert GeminiAdapter().scan_file_meta("/nonexistent/path/session.jsonl") == {}
