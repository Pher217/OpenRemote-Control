"""
render_digest(fleet_state) -> str

Pure function — no I/O, no DB, no network.  Takes the list produced by
build_fleet_state() (or a test-supplied list of the same shape) and returns
a plain-text centralised major-step digest suitable for posting to the
supervisor Telegram topic.

Groups (in order):
  1. Needs input  — waiting_approval  → operator action required
  2. Working      — running / starting / pending
  3. Idle         — anything else active

Each session renders as one line:
  <label> · <runtime_mode> · <status> · [⚠ needs input] · <age>

The entire output passes through redact.redact() before being returned
(Safety Contract #6 — redaction at post, S1 DoD: "❌ a digest leaks a secret").
"""

from __future__ import annotations

from datetime import timedelta

from apps.supervisor.fleet_state import SessionDict, group_fleet_state
from apps.supervisor.redact import redact


def _fmt_age(age: timedelta) -> str:
    """Format a timedelta as a human-readable age string."""
    total = int(age.total_seconds())
    if total < 0:
        return "0s"
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


def _session_line(s: SessionDict) -> str:
    """Render one session as a single digest line."""
    parts = [
        s["label"],
        s["runtime_mode"],
        s["status"].replace("_", " "),
    ]
    if s["needs_input"]:
        parts.append("⚠ needs input")
    parts.append(_fmt_age(s["age"]))
    return " · ".join(parts)


def render_digest(fleet_state: list[SessionDict]) -> str:
    """Render a centralised major-step digest from a fleet state snapshot.

    Returns "No active sessions." for an empty fleet.
    Output is redacted before returning.
    """
    if not fleet_state:
        return redact("No active sessions.")

    groups = group_fleet_state(fleet_state)
    lines: list[str] = [f"Fleet digest — {len(fleet_state)} active session(s)"]

    if groups["needs_input"]:
        lines.append("")
        lines.append("⚠ Needs input")
        for s in groups["needs_input"]:
            lines.append(f"  • {_session_line(s)}")

    if groups["working"]:
        lines.append("")
        lines.append("✅ Working")
        for s in groups["working"]:
            lines.append(f"  • {_session_line(s)}")

    if groups["idle"]:
        lines.append("")
        lines.append("⏸ Idle")
        for s in groups["idle"]:
            lines.append(f"  • {_session_line(s)}")

    return redact("\n".join(lines))
