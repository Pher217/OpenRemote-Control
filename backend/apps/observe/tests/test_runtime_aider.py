from apps.observe.runtimes import get_runtime_adapter
from apps.observe.runtimes.aider import AiderAdapter

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_runtime_adapter_aider():
    """
    GIVEN the provider string "aider"
    WHEN the registry resolves it
    THEN the Aider adapter is returned with file source kind
    """
    adapter = get_runtime_adapter("aider")
    assert adapter.provider == "aider"
    assert adapter.source_kind == "file"


# ---------------------------------------------------------------------------
# parse_turn — user turn
# ---------------------------------------------------------------------------


def test_parse_turn_user_line_returns_user_role():
    """
    GIVEN a line beginning with the user marker prefix
    WHEN parse_turn() consumes it
    THEN it returns a user turn carrying the observed-taint contract
    """
    raw = "#### Please review the billing logic."

    parsed = AiderAdapter().parse_turn(raw)

    assert parsed is not None
    assert parsed["role"] == "user"
    assert parsed["text"] == "Please review the billing logic."
    assert parsed["source"] == "aider"
    assert parsed["taint"] == "observed"
    assert parsed["uuid"] is None
    assert parsed["session_id"] is None


# ---------------------------------------------------------------------------
# parse_turn — assistant turn
# ---------------------------------------------------------------------------


def test_parse_turn_assistant_line_returns_assistant_role():
    """
    GIVEN a plain text line between user markers
    WHEN parse_turn() consumes it
    THEN it returns an assistant turn
    """
    raw = "I'll review the billing logic across the three modules."

    parsed = AiderAdapter().parse_turn(raw)

    assert parsed is not None
    assert parsed["role"] == "assistant"
    assert parsed["text"] == "I'll review the billing logic across the three modules."
    assert parsed["source"] == "aider"
    assert parsed["taint"] == "observed"


# ---------------------------------------------------------------------------
# parse_turn — skipped / non-conversational lines
# ---------------------------------------------------------------------------


def test_parse_turn_returns_none_on_empty_line():
    """
    GIVEN an empty line
    WHEN parse_turn() consumes it
    THEN it returns None (non-conversational)
    """
    assert AiderAdapter().parse_turn("") is None


def test_parse_turn_returns_none_on_structural_markdown():
    """
    GIVEN a markdown structural line
    WHEN parse_turn() consumes it
    THEN it returns None
    """
    assert AiderAdapter().parse_turn("---") is None


def test_parse_turn_returns_none_on_whitespace_only():
    """
    GIVEN a whitespace-only line
    WHEN parse_turn() consumes it
    THEN it returns None
    """
    assert AiderAdapter().parse_turn("   ") is None


# ---------------------------------------------------------------------------
# extract_session_meta
# ---------------------------------------------------------------------------


def test_extract_session_meta_returns_empty():
    """
    GIVEN any raw line from an Aider history file
    WHEN extract_session_meta() is called
    THEN it returns an empty dict (no inline metadata known)
    """
    assert AiderAdapter().extract_session_meta("#### hello") == {}
    assert AiderAdapter().extract_session_meta("some text") == {}


# ---------------------------------------------------------------------------
# scan_file_meta
# ---------------------------------------------------------------------------


def test_scan_file_meta_returns_empty_on_existing_file(tmp_path):
    """
    GIVEN an existing Aider history file
    WHEN scan_file_meta() reads it
    THEN it returns an empty dict (no file-level metadata known)
    """
    fixture = tmp_path / ".aider.chat.history.md"
    fixture.write_text("#### hello\nassistant text\n", encoding="utf-8")
    assert AiderAdapter().scan_file_meta(str(fixture)) == {}


def test_scan_file_meta_returns_empty_on_missing_file():
    """
    GIVEN a path that does not exist
    WHEN scan_file_meta() is called
    THEN it returns an empty dict without raising
    """
    assert AiderAdapter().scan_file_meta("/nonexistent/path/.aider.chat.history.md") == {}
