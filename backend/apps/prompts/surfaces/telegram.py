"""Telegram rendering layer for Prompt objects.

Produces inline_keyboard reply_markup dicts and parses callback_data.
callback_data format: p:{nonce}:{key}  (max 64 bytes enforced).
"""

_NONCE_LEN = 16  # uuid4().hex[:16]
_PREFIX = "p:"
_MAX_CB_BYTES = 64

_DEFAULT_APPROVAL_OPTIONS = [
    {"key": "approve", "label": "Approve"},
    {"key": "reject", "label": "Reject"},
    {"key": "defer", "label": "Defer"},
]


def _cb(nonce: str, key: str) -> str:
    data = f"{_PREFIX}{nonce}:{key}"
    assert len(data.encode()) <= _MAX_CB_BYTES, (
        f"callback_data exceeds 64 bytes: {data!r}"
    )
    return data


def build_reply_markup(prompt) -> dict | None:
    from apps.prompts.models import Prompt

    pt = prompt.prompt_type

    if pt == Prompt.PromptType.NOTICE or pt == Prompt.PromptType.FREE_TEXT:
        return None

    if pt == Prompt.PromptType.APPROVAL:
        options = prompt.options if prompt.options else _DEFAULT_APPROVAL_OPTIONS
        keyboard = [
            [{"text": opt["label"], "callback_data": _cb(prompt.nonce, opt["key"])}]
            for opt in options
        ]
        return {"inline_keyboard": keyboard}

    if pt == Prompt.PromptType.CHOICE_SINGLE:
        keyboard = [
            [{"text": opt["label"], "callback_data": _cb(prompt.nonce, opt["key"])}]
            for opt in prompt.options
        ]
        return {"inline_keyboard": keyboard}

    if pt == Prompt.PromptType.CHOICE_MULTI:
        rows = [
            [{"text": opt["label"], "callback_data": _cb(prompt.nonce, opt["key"])}]
            for opt in prompt.options
        ]
        rows.append(
            [{"text": "Confirm", "callback_data": _cb(prompt.nonce, "__confirm")}]
        )
        return {"inline_keyboard": rows}

    return None


def parse_callback(data: str) -> tuple[str, str] | None:
    """Parse 'p:{nonce}:{key}' -> (nonce, key).  Returns None if malformed."""
    if not data.startswith(_PREFIX):
        return None
    rest = data[len(_PREFIX):]
    # nonce is exactly _NONCE_LEN chars; key follows after the next ':'
    if len(rest) < _NONCE_LEN + 2:
        return None
    nonce = rest[:_NONCE_LEN]
    if rest[_NONCE_LEN] != ":":
        return None
    key = rest[_NONCE_LEN + 1:]
    if not key:
        return None
    return nonce, key
