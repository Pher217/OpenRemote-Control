"""
Lightweight secret-redaction for supervisor digest output.

Purpose: strip common secret patterns from text before the digest is posted
to Telegram (Safety Contract #6 — redaction at ingestion + at post, S1 DoD).

Scope: this module handles pattern-based redaction only.  The existing
apps/telegram/telegram_api.py:redact_token() handles Telegram-token-specific
redaction and is NOT replaced here — call both when preparing log text.

Patterns stripped:
  - OpenAI / Anthropic API keys:  sk-... or sk-ant-...
  - Generic bearer tokens in Authorization headers
  - GitHub PATs: ghp_... / github_pat_...
  - Generic high-entropy tokens: any 32+ char hex or base64 run

Text that does not match any pattern is returned unchanged.
"""

from __future__ import annotations

import re

# Ordered list of (compiled_pattern, replacement) pairs.
# More specific patterns first so they win over the generic fallback.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # OpenAI / Anthropic API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED]"),
    # GitHub PATs
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    # Authorization header values (Bearer / Token)
    (re.compile(r"(?i)(bearer|token)\s+[A-Za-z0-9_\-\.]{20,}"), r"\1 [REDACTED]"),
    # Generic 32+ char hex strings (e.g. HMAC secrets, session tokens)
    (re.compile(r"\b[0-9a-f]{32,}\b"), "[REDACTED]"),
    # Generic 32+ char base64-ish runs (exclude short UUIDs / paths)
    (re.compile(r"[A-Za-z0-9+/=]{40,}"), "[REDACTED]"),
]


def redact(text: str) -> str:
    """Apply all redaction patterns to *text* and return the sanitised string.

    Pure function — no I/O, no side effects.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
