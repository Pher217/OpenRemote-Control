import json
import sqlite3
from pathlib import Path

from apps.observe.runtimes import get_runtime_adapter
from apps.observe.runtimes.opencode import (
    OPENCODE_TABLE_MESSAGES,
    OPENCODE_TABLE_SESSIONS,
    OpenCodeAdapter,
)

# ---------------------------------------------------------------------------
# Fixture — build a tiny OpenCode-shaped SQLite db matching the adapter's
# assumed schema constants. TODO-VERIFY mirrors opencode.py: the real OpenCode
# schema is unverified; this fixture encodes the same assumptions the adapter
# reads, so a schema change breaks both together (visible in one place).
# ---------------------------------------------------------------------------


def _make_opencode_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        f"CREATE TABLE {OPENCODE_TABLE_SESSIONS} "
        "(id TEXT PRIMARY KEY, name TEXT, path TEXT, branch TEXT)"
    )
    conn.execute(
        f"CREATE TABLE {OPENCODE_TABLE_MESSAGES} "
        "(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)"
    )
    conn.execute(
        f"INSERT INTO {OPENCODE_TABLE_SESSIONS} VALUES (?, ?, ?, ?)",
        ("sess-1", "refactor auth", "/home/phil/dev/myrepo", "feature/auth"),
    )
    conn.executemany(
        f"INSERT INTO {OPENCODE_TABLE_MESSAGES} VALUES (?, ?, ?, ?)",
        [
            (1, "sess-1", "user", "rename the token field"),
            (2, "sess-1", "assistant", "Renamed it across 3 files."),
            (3, "sess-1", "system", "internal bookkeeping"),
        ],
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_runtime_adapter_opencode():
    """
    GIVEN the provider string "opencode"
    WHEN the registry resolves it
    THEN the OpenCode adapter is returned and declares the sqlite source kind
    """
    adapter = get_runtime_adapter("opencode")
    assert adapter.provider == "opencode"
    assert adapter.source_kind == "sqlite"


# ---------------------------------------------------------------------------
# discover — finds .db files under the scan root
# ---------------------------------------------------------------------------


def test_discover_finds_db_file(tmp_path):
    """
    GIVEN a scan root containing one OpenCode .db file
    WHEN discover() scans it
    THEN it returns one (path, mtime) pair for that db
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)

    found = OpenCodeAdapter().discover(tmp_path)

    assert len(found) == 1
    assert found[0][0] == str(db)
    assert isinstance(found[0][1], float)


# ---------------------------------------------------------------------------
# read_turns — incremental cursor over the messages table
# ---------------------------------------------------------------------------


def test_read_turns_returns_all_messages_on_first_poll(tmp_path):
    """
    GIVEN a fresh poll state (no last_msg_id)
    WHEN read_turns() reads the db
    THEN every message row is emitted as a JSON line and the cursor advances
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)

    lines, state = OpenCodeAdapter().read_turns(str(db), {})

    assert len(lines) == 3
    assert state["last_msg_id"] == 3


def test_read_turns_is_incremental(tmp_path):
    """
    GIVEN a poll state already advanced past the existing rows
    WHEN read_turns() polls again with no new messages
    THEN it returns no lines and leaves the cursor unchanged
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)
    adapter = OpenCodeAdapter()

    _, state = adapter.read_turns(str(db), {})
    lines, state2 = adapter.read_turns(str(db), state)

    assert lines == []
    assert state2["last_msg_id"] == 3


def test_read_turns_missing_db_is_graceful(tmp_path):
    """
    GIVEN a path with no database file
    WHEN read_turns() is called
    THEN it returns no lines and the unchanged state (no exception)
    """
    lines, state = OpenCodeAdapter().read_turns(str(tmp_path / "nope.db"), {"last_msg_id": 5})

    assert lines == []
    assert state == {"last_msg_id": 5}


# ---------------------------------------------------------------------------
# parse_turn — a read_turns line becomes an observer turn dict
# ---------------------------------------------------------------------------


def test_parse_turn_user_row_returns_user_role(tmp_path):
    """
    GIVEN a JSON line emitted by read_turns for a user message
    WHEN parse_turn() consumes it
    THEN it returns a user turn carrying the observed-taint contract
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)
    lines, _ = OpenCodeAdapter().read_turns(str(db), {})

    parsed = OpenCodeAdapter().parse_turn(lines[0])

    assert parsed is not None
    assert parsed["role"] == "user"
    assert parsed["text"] == "rename the token field"
    assert parsed["source"] == "opencode"
    assert parsed["taint"] == "observed"
    assert parsed["session_id"] == "sess-1"


def test_parse_turn_assistant_row_returns_assistant_role(tmp_path):
    """
    GIVEN a JSON line for an assistant message
    WHEN parse_turn() consumes it
    THEN it returns an assistant turn
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)
    lines, _ = OpenCodeAdapter().read_turns(str(db), {})

    parsed = OpenCodeAdapter().parse_turn(lines[1])

    assert parsed is not None
    assert parsed["role"] == "assistant"
    assert parsed["text"] == "Renamed it across 3 files."


def test_parse_turn_non_conversational_role_is_dropped(tmp_path):
    """
    GIVEN a JSON line for a non user/assistant role (e.g. system)
    WHEN parse_turn() consumes it
    THEN it returns None (only conversational turns stream)
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)
    lines, _ = OpenCodeAdapter().read_turns(str(db), {})

    parsed = OpenCodeAdapter().parse_turn(lines[2])

    assert parsed is None


def test_parse_turn_malformed_json_returns_none():
    """
    GIVEN a non-JSON string
    WHEN parse_turn() consumes it
    THEN it returns None without raising
    """
    assert OpenCodeAdapter().parse_turn("not json{") is None


# ---------------------------------------------------------------------------
# extract_session_meta — repo/branch/title from a read_turns line
# ---------------------------------------------------------------------------


def test_extract_session_meta_pulls_repo_branch_title(tmp_path):
    """
    GIVEN a JSON line carrying session path/branch/title
    WHEN extract_session_meta() reads it
    THEN repo (basename of cwd), branch, and title are returned
    """
    db = tmp_path / "store.db"
    _make_opencode_db(db)
    lines, _ = OpenCodeAdapter().read_turns(str(db), {})

    meta = OpenCodeAdapter().extract_session_meta(lines[0])

    assert meta["repo"] == "myrepo"
    assert meta["branch"] == "feature/auth"
    assert meta["title"] == "refactor auth"
    assert meta["session_id"] == "sess-1"
