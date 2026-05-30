import json


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
