"""Codex runtime adapter.

Locates ``*.jsonl`` transcript files under ``~/.codex/sessions`` and parses
``event_msg`` payloads (``user_message`` / ``agent_message``) into normalized conversation turns (role/text/uuid/session_id).
"""
import json
import os

from apps.observe.runtimes import JsonlScanMixin, _cwd_to_repo, register_runtime_adapter

_USER_EVENTS = {"user_message"}
_ASSISTANT_EVENTS = {"agent_message"}
_CONVERSATIONAL_EVENTS = _USER_EVENTS | _ASSISTANT_EVENTS


@register_runtime_adapter
class CodexAdapter(JsonlScanMixin):
    provider = "codex"
    source_kind = "file"
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
                meta["repo"] = _cwd_to_repo(cwd)
            git = payload.get("git")
            if isinstance(git, dict):
                branch = git.get("branch")
                if isinstance(branch, str):
                    meta["branch"] = branch

        elif outer_type == "turn_context" and isinstance(payload, dict):
            cwd = payload.get("cwd")
            if isinstance(cwd, str):
                meta["repo"] = _cwd_to_repo(cwd)

        return meta
