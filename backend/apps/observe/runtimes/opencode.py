import json
import os
import sqlite3
from pathlib import Path

from apps.observe.runtimes import register_runtime_adapter

# TODO-VERIFY: OpenCode DB schema is unverified. The following constants isolate
# every assumption so they can be updated once the real schema is known.

# --- DB file discovery ----------------------------------------------------
OPENCODE_DB_GLOB = "**/*.db"  # Assumed: one or more .db files under scan root

# --- Table names ----------------------------------------------------------
OPENCODE_TABLE_SESSIONS = "sessions"
OPENCODE_TABLE_MESSAGES = "messages"

# --- Column names (raw DB schema) -----------------------------------------
# sessions table
OPENCODE_DB_COL_SESSION_ID = "id"
OPENCODE_DB_COL_SESSION_NAME = "name"
OPENCODE_DB_COL_SESSION_PATH = "path"
OPENCODE_DB_COL_SESSION_BRANCH = "branch"

# messages table
OPENCODE_DB_COL_MSG_ID = "id"
OPENCODE_DB_COL_MSG_SESSION_ID = "session_id"
OPENCODE_DB_COL_MSG_ROLE = "role"
OPENCODE_DB_COL_MSG_CONTENT = "content"

# --- Aliases used in JOIN queries to avoid column name collisions -----------
OPENCODE_ALIAS_MSG_ID = "msg_id"

# --- Keys in the JSON dict passed between adapter methods -----------------
# read_turns serialises rows with these keys; parse_turn/extract_session_meta
# consume them.
OPENCODE_KEY_MSG_ID = "msg_id"
OPENCODE_KEY_SESSION_ID = "session_id"
OPENCODE_KEY_ROLE = "role"
OPENCODE_KEY_CONTENT = "content"
OPENCODE_KEY_CWD = "cwd"
OPENCODE_KEY_BRANCH = "git_branch"
OPENCODE_KEY_TITLE = "title"

# --- Assumed role values in the messages table ----------------------------
OPENCODE_ROLE_USER = "user"
OPENCODE_ROLE_ASSISTANT = "assistant"


@register_runtime_adapter
class OpenCodeAdapter:
    provider = "opencode"
    source_kind = "sqlite"
    default_root_env = "OBSERVE_OPENCODE_DB_DIR"
    default_root = os.path.expanduser("~/.opencode")
    discovery_glob = OPENCODE_DB_GLOB

    def _row_to_turn(self, obj: dict) -> dict | None:
        role = obj.get(OPENCODE_KEY_ROLE)
        if role == OPENCODE_ROLE_USER:
            observer_role = "user"
        elif role == OPENCODE_ROLE_ASSISTANT:
            observer_role = "assistant"
        else:
            return None
        text = obj.get(OPENCODE_KEY_CONTENT, "")
        if not isinstance(text, str):
            text = str(text)
        text = text.rstrip()
        if not text.strip():
            return None
        return {
            "role": observer_role,
            "text": text,
            "uuid": None,
            "session_id": obj.get(OPENCODE_KEY_SESSION_ID),
            "source": self.provider,
            "taint": "observed",
        }

    def parse_turn(self, raw: str) -> dict | None:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        return self._row_to_turn(obj)

    def extract_session_meta(self, raw: str) -> dict:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(obj, dict):
            return {}
        meta: dict = {}
        session_id = obj.get(OPENCODE_KEY_SESSION_ID)
        if isinstance(session_id, str):
            meta["session_id"] = session_id
        cwd = obj.get(OPENCODE_KEY_CWD)
        if isinstance(cwd, str):
            meta["repo"] = cwd.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
        branch = obj.get(OPENCODE_KEY_BRANCH)
        if isinstance(branch, str):
            meta["branch"] = branch
        title = obj.get(OPENCODE_KEY_TITLE)
        if isinstance(title, str):
            meta["title"] = title
        return meta

    def scan_file_meta(self, path: str) -> dict:
        merged: dict = {}
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                # nosec B608 — every interpolated token is a module-level schema constant
                # (OPENCODE_TABLE_* / OPENCODE_DB_COL_* / OPENCODE_KEY_*), never user input.
                f"SELECT {OPENCODE_DB_COL_SESSION_ID} AS {OPENCODE_KEY_SESSION_ID}, "  # nosec B608
                f"{OPENCODE_DB_COL_SESSION_PATH} AS {OPENCODE_KEY_CWD}, "
                f"{OPENCODE_DB_COL_SESSION_BRANCH} AS {OPENCODE_KEY_BRANCH}, "
                f"{OPENCODE_DB_COL_SESSION_NAME} AS {OPENCODE_KEY_TITLE} "
                f"FROM {OPENCODE_TABLE_SESSIONS}"
            )
            for row in cursor:
                row_dict = {
                    OPENCODE_KEY_SESSION_ID: row[OPENCODE_KEY_SESSION_ID],
                    OPENCODE_KEY_CWD: row[OPENCODE_KEY_CWD],
                    OPENCODE_KEY_BRANCH: row[OPENCODE_KEY_BRANCH],
                    OPENCODE_KEY_TITLE: row[OPENCODE_KEY_TITLE],
                }
                m = self.extract_session_meta(json.dumps(row_dict))
                m.pop("session_id", None)
                if m:
                    merged.update(m)
            conn.close()
        except (OSError, sqlite3.Error):
            return {}
        return merged

    def discover(self, root: Path) -> list[tuple[str, float]]:
        paths = list(root.glob(self.discovery_glob))
        return [(str(p), os.path.getmtime(p)) for p in paths]

    def read_turns(self, db_path: str, state: dict) -> tuple[list[str], dict]:
        """Query new messages from the SQLite DB.

        state: dict with optional 'last_msg_id' key.
        Returns: (list of JSON-encoded row dicts, updated state)
        """
        last_msg_id = state.get("last_msg_id", 0)
        lines: list[str] = []
        new_state = dict(state)
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                # nosec B608 — interpolated tokens are module-level schema constants; the only
                # dynamic value (last_msg_id) is bound via the ? placeholder in the WHERE clause.
                f"SELECT m.{OPENCODE_DB_COL_MSG_ID} AS {OPENCODE_ALIAS_MSG_ID}, "  # nosec B608
                f"m.{OPENCODE_DB_COL_MSG_SESSION_ID} AS {OPENCODE_KEY_SESSION_ID}, "
                f"m.{OPENCODE_DB_COL_MSG_ROLE} AS {OPENCODE_KEY_ROLE}, "
                f"m.{OPENCODE_DB_COL_MSG_CONTENT} AS {OPENCODE_KEY_CONTENT}, "
                f"s.{OPENCODE_DB_COL_SESSION_PATH} AS {OPENCODE_KEY_CWD}, "
                f"s.{OPENCODE_DB_COL_SESSION_BRANCH} AS {OPENCODE_KEY_BRANCH}, "
                f"s.{OPENCODE_DB_COL_SESSION_NAME} AS {OPENCODE_KEY_TITLE} "
                f"FROM {OPENCODE_TABLE_MESSAGES} m "
                f"JOIN {OPENCODE_TABLE_SESSIONS} s "
                f"ON m.{OPENCODE_DB_COL_MSG_SESSION_ID} = s.{OPENCODE_DB_COL_SESSION_ID} "
                f"WHERE m.{OPENCODE_DB_COL_MSG_ID} > ? "
                f"ORDER BY m.{OPENCODE_DB_COL_MSG_ID} ASC",
                (last_msg_id,),
            )
            max_msg_id = last_msg_id
            for row in cursor:
                max_msg_id = max(max_msg_id, row[OPENCODE_ALIAS_MSG_ID])
                row_dict = {
                    OPENCODE_KEY_MSG_ID: row[OPENCODE_ALIAS_MSG_ID],
                    OPENCODE_KEY_SESSION_ID: row[OPENCODE_KEY_SESSION_ID],
                    OPENCODE_KEY_ROLE: row[OPENCODE_KEY_ROLE],
                    OPENCODE_KEY_CONTENT: row[OPENCODE_KEY_CONTENT],
                    OPENCODE_KEY_CWD: row[OPENCODE_KEY_CWD],
                    OPENCODE_KEY_BRANCH: row[OPENCODE_KEY_BRANCH],
                    OPENCODE_KEY_TITLE: row[OPENCODE_KEY_TITLE],
                }
                lines.append(json.dumps(row_dict))
            conn.close()
        except (OSError, sqlite3.Error):
            return [], state

        new_state["last_msg_id"] = max_msg_id
        return lines, new_state
