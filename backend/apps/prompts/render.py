"""Plain-text rendering and reply parsing for Prompt objects.

Used by messaging surfaces that have no native button widgets (WhatsApp,
Slack, Discord, Signal, iMessage, etc.).  Prompts render as numbered
lists and users respond with a number or free text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.prompts.models import Prompt

_DEFAULT_APPROVAL_OPTIONS = [
    {"key": "allow", "label": "Allow"},
    {"key": "deny", "label": "Deny"},
]


def render_prompt(prompt: Prompt) -> str:
    from apps.prompts.models import Prompt as PromptModel

    pt = prompt.prompt_type
    question = prompt.question
    body = (prompt.body or "").strip()

    if pt == PromptModel.PromptType.NOTICE:
        parts = [question]
        if body:
            parts.append(body)
        return "\n".join(parts)

    header_parts = [question]
    if body:
        header_parts.append(body)
    header = "\n".join(header_parts)

    if pt == PromptModel.PromptType.APPROVAL:
        options = prompt.options if prompt.options else _DEFAULT_APPROVAL_OPTIONS
        numbered = "\n".join(
            f"{i + 1}. {opt['label']}" for i, opt in enumerate(options)
        )
        return f"{header}\n{numbered}\nReply with the number."

    if pt == PromptModel.PromptType.CHOICE_SINGLE:
        numbered = "\n".join(
            f"{i + 1}. {opt['label']}" for i, opt in enumerate(prompt.options)
        )
        return f"{header}\n{numbered}\nReply with the number."

    if pt == PromptModel.PromptType.CHOICE_MULTI:
        numbered = "\n".join(
            f"{i + 1}. {opt['label']}" for i, opt in enumerate(prompt.options)
        )
        return f"{header}\n{numbered}\nReply with numbers separated by commas."

    if pt == PromptModel.PromptType.FREE_TEXT:
        return f"{header}\nReply with your answer."

    return header


def parse_reply(prompt: Prompt, text: str) -> dict | None:
    """Map a user's plain-text reply to a resolve() kwargs dict.

    Returns:
        {"option_keys": [...]}  for choice/approval types
        {"text": "..."}         for free_text
        None                    if the reply cannot be mapped
    """
    from apps.prompts.models import Prompt as PromptModel

    pt = prompt.prompt_type
    stripped = (text or "").strip()

    if pt == PromptModel.PromptType.FREE_TEXT:
        if not stripped:
            return None
        return {"text": stripped}

    if pt == PromptModel.PromptType.NOTICE:
        return None

    if pt == PromptModel.PromptType.APPROVAL:
        options = prompt.options if prompt.options else _DEFAULT_APPROVAL_OPTIONS
        return _parse_choice(stripped, options, multi=False)

    if pt == PromptModel.PromptType.CHOICE_SINGLE:
        return _parse_choice(stripped, prompt.options, multi=False)

    if pt == PromptModel.PromptType.CHOICE_MULTI:
        return _parse_choice(stripped, prompt.options, multi=True)

    return None


# ---------------------------------------------------------------------------
# Approval aliases accepted from the user
# ---------------------------------------------------------------------------
_APPROVAL_ALIASES: dict[str, str] = {
    "allow": "allow",
    "approve": "allow",
    "yes": "allow",
    "deny": "deny",
    "reject": "deny",
    "no": "deny",
}


def _parse_choice(text: str, options: list, *, multi: bool) -> dict | None:
    """Parse a number, a comma-separated list of numbers, or a label match."""
    if not options:
        return None

    # Build lookup structures
    by_label: dict[str, str] = {
        opt["label"].lower(): opt["key"] for opt in options
    }
    all_keys: list[str] = [opt["key"] for opt in options]

    # --- Single-value path (also handles approval aliases) ---
    if not multi:
        # Numeric: "1"
        key = _try_numeric(text, options)
        if key is not None:
            return {"option_keys": [key]}

        # Alias (allow/deny/approve/reject) — only relevant for approval
        alias = _APPROVAL_ALIASES.get(text.lower())
        if alias is not None and alias in all_keys:
            return {"option_keys": [alias]}

        # Exact label
        key = by_label.get(text.lower())
        if key is not None:
            return {"option_keys": [key]}

        return None

    # --- Multi-value path ---
    raw_parts = [p.strip() for p in text.split(",") if p.strip()]
    if not raw_parts:
        return None

    resolved: list[str] = []
    for part in raw_parts:
        key = _try_numeric(part, options)
        if key is None:
            key = by_label.get(part.lower())
        if key is None:
            return None
        resolved.append(key)

    return {"option_keys": resolved}


def _try_numeric(text: str, options: list) -> str | None:
    try:
        idx = int(text) - 1
    except (ValueError, TypeError):
        return None
    if 0 <= idx < len(options):
        return options[idx]["key"]
    return None
