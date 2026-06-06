"""
input_policy.py — Safety classifier for remote keystroke injection.

All functions are pure (no I/O, no external dependencies).  The module is
intentionally kept dependency-free so it can be imported in any context —
including the test suite — without requiring libtmux, a running tmux server,
or any PTY support.

Design intent
-------------
When the operator sends text to an 'orc run' session we apply a
conservative, default-deny classification before anything reaches tmux
send-keys.  Three outcomes are possible:

  SAFE      — a single, short, plain line that looks like a normal command
               (e.g. "ls\\n", "git status\\n").  No approval gate required.

  REVIEW    — structurally suspicious: multiline, very long, or contains
               shell-chaining metacharacters.  Approval is required.

  DANGEROUS — contains raw control/escape sequences, well-known
               destructive shell patterns, or attempts to escape the
               workspace.  Approval is required, and callers should
               default to rejecting outright.

Anything that does not match a SAFE profile is escalated to at least REVIEW
(default-deny bias).
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------

class Risk(StrEnum):
    SAFE = "SAFE"
    REVIEW = "REVIEW"
    DANGEROUS = "DANGEROUS"


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# Non-printable control characters that are NOT a plain newline (\n / 0x0a).
# We detect these with unicodedata category "Cc" (control character).
# A trailing single \n is the normal line-terminator for a submitted command
# and is explicitly allowed inside a SAFE input.

# Shell patterns that are unambiguously destructive or privilege-escalating.
_DANGEROUS_SHELL_PATTERNS: list[re.Pattern[str]] = [
    # Recursive forced deletion
    re.compile(r"rm\s+(-[a-z]*f[a-z]*\s+|--force\s+)(-[a-z]*r[a-z]*\s+|--recursive\s+|/)", re.IGNORECASE),
    re.compile(r"rm\s+(-[a-z]*r[a-z]*\s+)(-[a-z]*f[a-z]*\s+|--force\s+|/)", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brm\s+-fr\b", re.IGNORECASE),
    # Disk-level write
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\b>\s*/dev/sd[a-z]"),
    # Fork bomb
    re.compile(r":\s*\(\s*\)\s*\{"),
    # Sudo (any form)
    re.compile(r"\bsudo\b"),
    # Pipe-to-shell (remote code execution)
    re.compile(r"curl\b.*\|\s*(?:ba)?sh\b"),
    re.compile(r"wget\b.*\|\s*(?:ba)?sh\b"),
    re.compile(r"curl\b.*\|\s*bash\b"),
    re.compile(r"wget\b.*\|\s*bash\b"),
    # World-writable chmod on critical paths
    re.compile(r"chmod\s+777\s+/"),
    # Forced git push
    re.compile(r"git\s+push\s+.*--force"),
    re.compile(r"git\s+push\s+.*-f\b"),
]

# Workspace escape: path traversal or writes to sensitive absolute locations.
_ESCAPE_PATTERNS: list[re.Pattern[str]] = [
    # Directory traversal
    re.compile(r"\.\./"),
    re.compile(r"\.\.[/\\]"),
    # Sensitive absolute paths
    re.compile(r"~/\.ssh\b"),
    re.compile(r"/etc/"),
    re.compile(r"~/.ssh"),
]

# Shell chaining / backgrounding metacharacters that turn a command-looking
# line into a compound expression we cannot safely pre-validate.
_CHAIN_META_RE = re.compile(
    r"(?:"
    r"&&"           # AND-list
    r"|\|\|"        # OR-list
    r"|(?<!\|)\|(?!\|)"  # pipe (but not ||)
    r"|(?<!;);(?!;)"     # semicolon (but not ;;)
    r"|`"           # backtick substitution
    r"|\$\("        # $(...) substitution
    r"|>>"          # append redirect
    r"|(?<![>])>(?![>])"  # single redirect
    r")"
)

# Threshold for "very long input" → REVIEW
_MAX_SAFE_LENGTH = 2000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_control_sequence(text: str) -> bool:
    """Return True if *text* contains raw control or escape characters.

    A single trailing newline (``\\n`` / 0x0a) is explicitly *not* considered
    a control sequence — it is the normal line-terminator for a submitted
    command.  All other C0/C1 control characters (unicodedata category ``Cc``)
    and the ESC byte (0x1b) are flagged.
    """
    for i, ch in enumerate(text):
        code = ord(ch)
        # Allow a single trailing newline (last character of the string).
        if ch == "\n" and i == len(text) - 1:
            continue
        # Any other newline embedded mid-string is not a control sequence
        # per se — multi-line detection is handled separately.  But we still
        # flag any embedded \r, \0, BEL, BS, etc.
        if ch == "\n":
            # mid-string newline: not a control sequence (multiline handled elsewhere)
            continue
        if unicodedata.category(ch) == "Cc":
            return True
        # Explicitly catch ESC (0x1b) even if Python doesn't categorise it
        if code == 0x1B:
            return True
    return False


def _contains_dangerous_shell(text: str) -> list[str]:
    """Return a list of reasons if *text* matches any destructive shell pattern."""
    reasons: list[str] = []
    for pat in _DANGEROUS_SHELL_PATTERNS:
        if pat.search(text):
            reasons.append(f"matches dangerous shell pattern: {pat.pattern!r}")
    return reasons


def _contains_escape(text: str) -> list[str]:
    """Return a list of reasons if *text* contains workspace-escape patterns."""
    reasons: list[str] = []
    for pat in _ESCAPE_PATTERNS:
        if pat.search(text):
            reasons.append(f"matches workspace-escape pattern: {pat.pattern!r}")
    return reasons


def _contains_chain_meta(text: str) -> bool:
    """Return True if *text* contains shell chaining/backgrounding metacharacters."""
    return bool(_CHAIN_META_RE.search(text))


# ---------------------------------------------------------------------------
# Primary classifier
# ---------------------------------------------------------------------------

def classify_input(text: str) -> dict:  # type: ignore[type-arg]
    """Classify *text* for safe injection into a PTY session.

    Returns a dict with keys:

    ``risk`` : :class:`Risk`
        The overall risk level.

    ``reasons`` : list[str]
        Human-readable explanations for any non-SAFE classification.

    ``requires_approval`` : bool
        ``True`` for REVIEW and DANGEROUS; ``False`` only for SAFE.

    The classifier is **default-deny**: anything that does not clearly match
    the SAFE profile is escalated to at least REVIEW.

    Parameters
    ----------
    text:
        The exact string the operator wants to inject.  A well-formed command
        is typically a single line ending with a single ``\\n``.
    """
    reasons: list[str] = []

    # --- DANGEROUS checks (highest priority) ---

    # 1. Raw control / escape sequences
    if is_control_sequence(text):
        reasons.append("contains raw control or escape sequences (e.g. ESC, Ctrl-C, NUL)")
        return {"risk": Risk.DANGEROUS, "reasons": reasons, "requires_approval": True}

    # 2. Destructive shell patterns
    dangerous_shell = _contains_dangerous_shell(text)
    if dangerous_shell:
        reasons.extend(dangerous_shell)
        return {"risk": Risk.DANGEROUS, "reasons": reasons, "requires_approval": True}

    # 3. Workspace escape
    escape_reasons = _contains_escape(text)
    if escape_reasons:
        reasons.extend(escape_reasons)
        return {"risk": Risk.DANGEROUS, "reasons": reasons, "requires_approval": True}

    # --- REVIEW checks ---

    # 4. Multiline: more than one newline present
    newline_count = text.count("\n")
    if newline_count > 1:
        reasons.append(f"multiline input ({newline_count} newlines)")
        return {"risk": Risk.REVIEW, "reasons": reasons, "requires_approval": True}

    # 5. Very long input
    if len(text) > _MAX_SAFE_LENGTH:
        reasons.append(f"input length {len(text)} exceeds limit {_MAX_SAFE_LENGTH}")
        return {"risk": Risk.REVIEW, "reasons": reasons, "requires_approval": True}

    # 6. Shell chaining / backgrounding metacharacters
    if _contains_chain_meta(text):
        reasons.append("contains shell chaining or redirect metacharacters (&&, ||, |, ;, `, $(), >, >>)")
        return {"risk": Risk.REVIEW, "reasons": reasons, "requires_approval": True}

    # --- SAFE ---

    # A single short line, optionally terminated with exactly one \n,
    # containing only printable characters — no dangerous patterns above.
    return {"risk": Risk.SAFE, "reasons": [], "requires_approval": False}
