"""
Proactive-push formatting for the Fleet Supervisor major-step classifier (S2).

Pure function — no I/O, no DB, no model calls.

Takes the list of MajorStep dicts produced by classifier.detect_major_steps()
and returns a short human-readable push string suitable for the supervisor
Telegram topic.

Safety Contract #6 / S0.2 / S2.3:
  - All session labels are passed through redact.redact() before inclusion in
    any outbound string.  Labels are display-only; the trigger logic lives in
    classifier.py and is content-blind.
  - coalesce() de-duplicates per thread_id (highest severity wins) and caps
    total volume at MAX_PUSH_PER_CYCLE per cycle to prevent fatigue-flooding
    (abuse case F5).
"""

from __future__ import annotations

from apps.supervisor.classifier import MAX_PUSH_PER_CYCLE, MajorStep, StepKind, severity_key
from apps.supervisor.redact import redact

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_KIND_EMOJI: dict[str, str] = {
    StepKind.NEEDS_INPUT: "⚠",
    StepKind.FINISHED: "✅",
    StepKind.STARTED: "▶",
    StepKind.STALLED: "⏳",
}

_KIND_LABEL: dict[str, str] = {
    StepKind.NEEDS_INPUT: "needs input",
    StepKind.FINISHED: "finished",
    StepKind.STARTED: "started",
    StepKind.STALLED: "stalled",
}


def _format_step(step: MajorStep) -> str:
    """Format a single MajorStep as one push line.

    The session label is redacted (Safety Contract #6) before inclusion.
    Only kind and the redacted label appear in the output — NO session content,
    command bodies, or turn text (v6 §10 minimal payload).
    """
    emoji = _KIND_EMOJI.get(step["kind"], "•")
    kind_label = _KIND_LABEL.get(step["kind"], step["kind"])
    safe_label = redact(step["label"])
    return f"{emoji} {safe_label} — {kind_label}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def coalesce(steps: list[MajorStep]) -> list[MajorStep]:
    """De-duplicate and cap major steps for one push cycle.

    Rules:
      1. Per thread_id, keep only the highest-severity step (de-dup).
      2. Sort all remaining steps descending by severity (highest urgency first).
      3. Cap total count at MAX_PUSH_PER_CYCLE (anti-fatigue, abuse case F5).

    Returns a new list; the input is not mutated.
    """
    # De-duplicate: per thread_id, keep highest severity
    best: dict[str, MajorStep] = {}
    for step in steps:
        tid = step["thread_id"]
        existing = best.get(tid)
        if existing is None or step["severity"] > existing["severity"]:
            best[tid] = step

    # Sort descending by severity (highest urgency first)
    sorted_steps = sorted(best.values(), key=severity_key, reverse=True)

    # Cap at MAX_PUSH_PER_CYCLE
    return sorted_steps[:MAX_PUSH_PER_CYCLE]


def format_push(steps: list[MajorStep]) -> str:
    """Format a list of major steps into a proactive-push string.

    Steps are expected to be pre-coalesced (call coalesce() first).  If the
    list is empty, returns an empty string (no push needed).

    Output is ordered by descending severity (highest urgency at top).
    Each line: <emoji> <redacted-label> — <kind>
    """
    if not steps:
        return ""

    # Defensive sort in case caller didn't coalesce first
    ordered = sorted(steps, key=severity_key, reverse=True)
    lines = ["🔔 Fleet update"]
    for step in ordered:
        lines.append(f"  {_format_step(step)}")
    return "\n".join(lines)
