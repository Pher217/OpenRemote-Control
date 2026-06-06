import json
import os

from apps.observe.runtimes import register_runtime_adapter

# Gemini CLI JSONL format (documented, not verified against a live sample — no
# ~/.gemini/tmp/ directory was found on this machine).
#
# Each line in a session-*.jsonl file is one of:
#   {"type": "session_metadata", "session_id": "...", ...}
#   {"type": "user",    "session_id": "...", "content": {"text": "..."}}
#   {"type": "gemini",  "session_id": "...", "content": {"text": "..."}}
#   {"type": "message_update", ...}  # partial stream chunk — skip
#
# The file path carries project context via the directory hash:
#   ~/.gemini/tmp/<project_hash>/chats/session-<id>.jsonl


@register_runtime_adapter
class GeminiAdapter:
    provider = "gemini"
    default_root_env = "OBSERVE_GEMINI_TMP_DIR"
    default_root = os.path.expanduser("~/.gemini/tmp")
    discovery_glob = "**/chats/*.jsonl"

    def _extract_text(self, record: dict) -> str:
        """Pull text out of a Gemini JSONL record defensively.

        Handles the documented shape ``{"content": {"text": "..."}}`` as well as
        a flat ``{"text": "..."}`` at the top level and a ``parts``-style list
        (``{"content": {"parts": [{"text": "..."}]}}``) that mirrors the Gemini
        REST API shape some CLI versions emit.
        """
        # Top-level text shortcut
        top_text = record.get("text")
        if isinstance(top_text, str):
            return top_text.rstrip()

        content = record.get("content")
        if isinstance(content, str):
            return content.rstrip()
        if not isinstance(content, dict):
            return ""

        # content.text
        text = content.get("text")
        if isinstance(text, str):
            return text.rstrip()

        # content.parts[*].text (Gemini REST-style)
        parts = content.get("parts")
        if isinstance(parts, list):
            segments = [
                p["text"]
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            return "\n".join(segments).rstrip()

        return ""

    def parse_turn(self, raw: str) -> dict | None:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None

        record_type = obj.get("type")

        # Only surface complete user/gemini turns; skip metadata and partials.
        if record_type == "user":
            role = "user"
        elif record_type == "gemini":
            role = "assistant"
        else:
            return None

        text = self._extract_text(obj)
        if not text.strip():
            return None

        return {
            "role": role,
            "text": text,
            "uuid": obj.get("uuid") or obj.get("id") or None,
            "session_id": obj.get("session_id") or obj.get("sessionId"),
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

        # session_id — prefer explicit field; fall back to sessionId convention
        session_id = obj.get("session_id") or obj.get("sessionId")
        if isinstance(session_id, str):
            meta["session_id"] = session_id

        # Gemini CLI stores the cwd / project root in session_metadata lines
        cwd = obj.get("cwd") or obj.get("workingDirectory")
        if isinstance(cwd, str):
            # Cross-platform basename: normalise backslashes so a Windows path
            # observed on macOS/Linux still yields the rightmost component.
            meta["repo"] = cwd.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]

        branch = obj.get("gitBranch") or obj.get("branch")
        if isinstance(branch, str):
            meta["branch"] = branch

        title = obj.get("title") or obj.get("chatTitle")
        if isinstance(title, str):
            meta["title"] = title

        return meta

    def scan_file_meta(self, path: str) -> dict:
        """Iterate every line of a JSONL session file and merge session metadata.

        The session_id is stripped from the merged result (it belongs on the
        individual turn, not the file-level summary) — mirroring claude_code.py.
        Returns {} on any OSError (missing file, permission denied, etc.).
        """
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
