import json
import os


def extract_text(content) -> str:
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


def parse_line(raw: str) -> dict | None:
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
    text = extract_text(message.get("content"))
    if not text.strip():
        return None
    return {
        "role": message.get("role") or obj_type,
        "text": text,
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
    }


def extract_session_meta(raw: str) -> dict:
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
        meta["repo"] = os.path.basename(cwd.rstrip("/\\"))
    branch = obj.get("gitBranch")
    if isinstance(branch, str):
        meta["branch"] = branch
    if obj.get("type") == "ai-title":
        title = obj.get("aiTitle")
        if isinstance(title, str):
            meta["title"] = title
    return meta


def scan_file_meta(path) -> dict:
    merged: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = extract_session_meta(line)
                m.pop("session_id", None)
                if m:
                    merged.update(m)
    except OSError:
        return {}
    return merged
