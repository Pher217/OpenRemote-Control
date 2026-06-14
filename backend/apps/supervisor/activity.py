"""
render_fleet_with_activity(fleet_state) -> str

Extends render_digest() output with per-session recent activity (last
MAX_TURNS_PER_SESSION turns from Message.redacted_content).

Security Contract #6 / S0.2:
  - ONLY Message.redacted_content is read — raw_content_encrypted is NEVER
    accessed anywhere in this module.
  - IMPORTANT: despite its name, `redacted_content` is NOT guaranteed to be
    redacted at write time — the current write paths (apps/threads/dispatch.py,
    apps/observe/service.py, apps/connectors/service.py) store raw turn text into
    it. So the redact() call below is the ACTIVE redaction boundary for this
    outbound path, not mere defense-in-depth. It strips known secret patterns
    (regex-based); it is not a guarantee of full content sanitisation.
  - Content is truncated to MAX_CHARS_PER_TURN before inclusion.
  - MAX_TOTAL_ACTIVITY_CHARS caps the appended activity block (headers + turn
    lines) beyond the base fleet digest, to prevent context explosion.
  - This function is READ-ONLY context building — no action or tool capability.
"""

from __future__ import annotations

from apps.supervisor.digest import render_digest
from apps.supervisor.fleet_state import SessionDict, build_fleet_state
from apps.supervisor.redact import redact

# ── Caps (named constants so tests can inspect and callers can override) ──────

#: Maximum number of recent turns to include per session.
MAX_TURNS_PER_SESSION: int = 2

#: Maximum characters to include from a single turn's redacted_content.
MAX_CHARS_PER_TURN: int = 240

#: Hard overall cap on the total activity text appended across all sessions,
#: so a wide fleet cannot inflate the LLM context without bound.
MAX_TOTAL_ACTIVITY_CHARS: int = 4000


def _recent_turns(thread_id: str) -> list[tuple[str, str]]:
    """Return the last MAX_TURNS_PER_SESSION turns for a session.

    Returns a list of (role, truncated_redacted_content) tuples ordered from
    oldest-to-newest among the selected turns.

    SECURITY: queries ONLY redacted_content — raw_content_encrypted is never
    read or referenced.
    """
    # Import here to keep module-level imports free of Django ORM until called.
    from apps.threads.models import Message  # noqa: PLC0415

    # Order by -sequence to get the LAST N, then reverse for chronological display.
    # Only redacted_content is fetched — raw_content_encrypted is excluded.
    rows = (
        Message.objects
        .filter(thread_id=thread_id)
        .order_by("-sequence")
        .values("role", "redacted_content")
        [:MAX_TURNS_PER_SESSION]
    )
    turns = []
    for row in reversed(list(rows)):
        content = row["redacted_content"] or ""
        # ACTIVE redaction boundary: redacted_content is NOT actually redacted at
        # write time (see module docstring), so this strips known secret patterns
        # before the content enters the LLM context / outbound message.
        content = redact(content)
        # Truncate to cap.
        if len(content) > MAX_CHARS_PER_TURN:
            content = content[:MAX_CHARS_PER_TURN] + "…"
        turns.append((row["role"], content))
    return turns


def render_fleet_with_activity(
    fleet_state: list[SessionDict] | None = None,
) -> str:
    """Build the fleet digest plus recent activity for each session.

    Calls build_fleet_state() once (unless fleet_state is provided, for
    testability) and delegates the header/status lines to render_digest().

    Returns the augmented string: digest lines + indented 'recent:' block
    per session.  The entire output is redacted before returning.
    """
    if fleet_state is None:
        fleet_state = build_fleet_state()

    # Delegate the header + per-session status lines to the existing function.
    base_digest = render_digest(fleet_state)

    if not fleet_state:
        # No sessions — render_digest already returns "No active sessions."
        return base_digest

    # Build the augmented output line by line.
    lines: list[str] = [base_digest]
    total_chars = 0

    for session in fleet_state:
        if total_chars >= MAX_TOTAL_ACTIVITY_CHARS:
            break

        turns = _recent_turns(session["thread_id"])
        if not turns:
            continue

        # Count the header toward the cap too (codex finding: headers were
        # previously uncounted, so the advertised cap could be exceeded).
        header = f"\n[{session['label']} — recent activity]"
        lines.append(header)
        total_chars += len(header)
        for role, content in turns:
            if total_chars >= MAX_TOTAL_ACTIVITY_CHARS:
                lines.append("  … (activity cap reached)")
                break
            line = f"  {role}: {content}"
            lines.append(line)
            total_chars += len(line)

    return redact("\n".join(lines))
