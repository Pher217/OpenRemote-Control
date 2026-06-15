"""Claude Code runtime adapter.

Locates ``*.jsonl`` transcript files under ``~/.claude/projects`` and parses
user/assistant JSONL records into normalized conversation turns (role/text/uuid/session_id).
"""
import json
import os

from apps.observe.runtimes import JsonlScanMixin, _cwd_to_repo, register_runtime_adapter


@register_runtime_adapter
class ClaudeCodeAdapter(JsonlScanMixin):
    provider = "claude_code"
    source_kind = "file"
    default_root_env = "OBSERVE_CLAUDE_PROJECTS_DIR"
    default_root = os.path.expanduser("~/.claude/projects")
    discovery_glob = "**/*.jsonl"

    def extract_text(self, content) -> str:
        if isinstance(content, str):
            return content.rstrip()
        if isinstance(content, list):
            parts = [
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            return "\n".join(parts).rstrip()
        return ""

    def parse_turn(self, raw: str) -> dict | None:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        obj_type = obj.get("type")
        if obj_type not in {"user", "assistant"}:
            return None
        message = obj.get("message")
        if not isinstance(message, dict):
            return None
        text = self.extract_text(message.get("content"))
        if not text.strip():
            return None
        return {
            "role": message.get("role") or obj_type,
            "text": text,
            "uuid": obj.get("uuid"),
            "session_id": obj.get("sessionId"),
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
        meta: dict = {}
        session_id = obj.get("sessionId")
        if isinstance(session_id, str):
            meta["session_id"] = session_id
        cwd = obj.get("cwd")
        if isinstance(cwd, str):
            # Cross-platform basename: split on both separators so a Windows cwd
            # (c:\Users\...\repo) yields the repo name when observed on macOS/Linux too.
            meta["repo"] = _cwd_to_repo(cwd)
        branch = obj.get("gitBranch")
        if isinstance(branch, str):
            meta["branch"] = branch
        if obj.get("type") == "ai-title":
            title = obj.get("aiTitle")
            if isinstance(title, str):
                meta["title"] = title
        return meta

