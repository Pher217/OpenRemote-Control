import json
import os

from apps.observe.runtimes import register_runtime_adapter

_USER_EVENTS = {"user_message"}
_ASSISTANT_EVENTS = {"agent_message"}
_CONVERSATIONAL_EVENTS = _USER_EVENTS | _ASSISTANT_EVENTS


@register_runtime_adapter
class CodexAdapter:
    provider = "codex"
    default_root_env = "OBSERVE_CODEX_SESSIONS_DIR"
    default_root = os.path.expanduser("~/.codex/sessions")
    discovery_glob = "**/*.jsonl"

    def parse_turn(self, raw: str) -> dict | None:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        if obj.get("type") != "event_msg":
            return None
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return None
        ptype = payload.get("type")
        if ptype not in _CONVERSATIONAL_EVENTS:
            return None
        message = payload.get("message")
        if not isinstance(message, str):
            return None
        text = message.rstrip()
        if not text.strip():
            return None
        role = "user" if ptype in _USER_EVENTS else "assistant"
        return {
            "role": role,
            "text": text,
            "uuid": None,
            "session_id": None,
            "source": self.provider,
            "taint": "observed",
        }

    def extract_session_meta(self, raw: str) -> dict:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(obj, dict):
            return {}
        outer_type = obj.get("type")
        payload = obj.get("payload")
        meta: dict = {}

        if outer_type == "session_meta" and isinstance(payload, dict):
            session_id = payload.get("id")
            if isinstance(session_id, str):
                meta["session_id"] = session_id
            cwd = payload.get("cwd")
            if isinstance(cwd, str):
                meta["repo"] = cwd.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
            git = payload.get("git")
            if isinstance(git, dict):
                branch = git.get("branch")
                if isinstance(branch, str):
                    meta["branch"] = branch

        elif outer_type == "turn_context" and isinstance(payload, dict):
            cwd = payload.get("cwd")
            if isinstance(cwd, str):
                meta["repo"] = cwd.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]

        return meta

    def scan_file_meta(self, path: str) -> dict:
        merged: dict = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    m = self.extract_session_meta(line)
                    m.pop("session_id", None)
                    if m:
                        merged.update(m)
        except OSError:
            return {}
        return merged
