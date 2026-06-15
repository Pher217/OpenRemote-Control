"""Render observed session turns as Telegram-supported HTML.

Telegram's HTML parse mode supports only these tags:
b, i, u, s, a, code, pre, blockquote, tg-spoiler. Everything outside a code
tag must have &, <, > escaped. Inside <code>/<pre> the same three are escaped
and no nested tags are allowed. href values must also escape &.

Pure functions only — no Django imports — so this module is fully unit-tested
without a database or network.
"""

import re

from apps.core.html import _esc

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?<!href=\")(?<!>)(https?://[^\s<]+)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def _convert_text(text: str) -> str:
    """Convert a non-code Markdown segment to Telegram HTML (escaped first)."""
    out = _esc(text)
    out = _HEADING_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _MD_LINK_RE.sub(
        lambda m: f'<a href="{_esc(m.group(2))}">{m.group(1)}</a>', out
    )
    out = _BARE_URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', out)
    out = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    return out


def _convert_non_code(segment: str) -> str:
    """Process a segment that may contain inline code spans."""
    parts = []
    last = 0
    for m in _INLINE_CODE_RE.finditer(segment):
        parts.append(_convert_text(segment[last : m.start()]))
        parts.append(f"<code>{_esc(m.group(1))}</code>")
        last = m.end()
    parts.append(_convert_text(segment[last:]))
    return "".join(parts)


def md_to_telegram_html(md: str) -> str:
    """Convert GitHub-flavored Markdown to Telegram HTML.

    Fenced code blocks are split out first so their contents are never treated
    as markdown, then inline code, then the remaining text is converted.
    """
    out = []
    last = 0
    for m in _FENCE_RE.finditer(md):
        out.append(_convert_non_code(md[last : m.start()]))
        out.append(f"<pre>{_esc(m.group(1))}</pre>")
        last = m.end()
    out.append(_convert_non_code(md[last:]))
    return "".join(out)


def format_turn(parsed, *, user_label, assistant_label, max_len=4096) -> str:
    role = parsed["role"]
    if role == "user":
        emoji, label = "🧑", user_label
    else:
        emoji, label = "🤖", assistant_label

    text = parsed["text"]
    if len(text) > 3500:
        text = text[:3500] + "…"

    header = f"<b>{emoji} {_esc(label)}</b>\n"
    return header + md_to_telegram_html(text)
