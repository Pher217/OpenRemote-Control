"""
Major-step classifier for the Fleet Supervisor (S2).

Pure functions over fleet state snapshot — no I/O, no DB, no model calls.

A "major step" is triggered ONLY by a server-observed structural fact:
  - a status transition (prev status → curr status comparison)
  - a new session appearing in the fleet (started kind)
  - a stall detected by comparing last_event_at timestamps across snapshots

Safety Contract S0.2 / abuse-case F1:
  The classifier NEVER inspects session *content* (labels, command bodies,
  turn text).  The diff is structural: thread_id, status, last_event_at.
  The label field is included in MajorStep only for display purposes and is
  processed by push.py::redact before any outbound post.  No content field
  drives trigger logic — that logic lives exclusively in detect_major_steps().
"""

from __future__ import annotations

from datetime import timedelta
from enum import IntEnum
from typing import TypedDict

from apps.supervisor.fleet_state import SessionDict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How long a non-terminal session must be event-silent before it is flagged
# as stalled.  Named constant so tests can assert boundary behaviour.
STALL_THRESHOLD: timedelta = timedelta(minutes=10)

# Maximum major-step notices emitted per classifier invocation.  Defined here
# so the push layer can import it without a circular dependency.
MAX_PUSH_PER_CYCLE: int = 5

# ---------------------------------------------------------------------------
# Kind and severity
# ---------------------------------------------------------------------------


class StepKind:
    """Server-defined major-step kinds.  String constants (not StrEnum) so they
    serialise cleanly without import overhead in callers."""

    NEEDS_INPUT = "needs_input"   # session entered waiting_approval
    FINISHED = "finished"         # session entered a terminal status (completed / failed / stopped)
    STARTED = "started"           # new session since prev snapshot
    STALLED = "stalled"           # no last_event_at change for >= STALL_THRESHOLD while non-terminal


class Severity(IntEnum):
    """Ordinal severity — higher value = higher urgency.

    Ordering (spec S2.3): needs_input > failed > finished > started > stalled.
    The FINISHED kind covers both completed and stopped; the failed sub-case is
    surfaced via the label in MajorStep but gets its own severity bucket so it
    sorts above ordinary finishes.
    """

    STALLED = 1
    STARTED = 2
    FINISHED = 3       # completed / stopped
    FAILED = 4         # failed specifically
    NEEDS_INPUT = 5


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


class MajorStep(TypedDict):
    thread_id: str
    label: str          # display name only — redacted by push.py before outbound post
    kind: str           # one of StepKind.*
    severity: int       # one of Severity.*


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = frozenset(["completed", "failed", "stopped"])


def _severity_for(kind: str, status: str) -> int:
    """Return the Severity int for a given kind + status pair.

    For FINISHED, we distinguish failed (higher urgency) from other terminals.
    """
    if kind == StepKind.NEEDS_INPUT:
        return Severity.NEEDS_INPUT
    if kind == StepKind.FINISHED:
        return Severity.FAILED if status == "failed" else Severity.FINISHED
    if kind == StepKind.STARTED:
        return Severity.STARTED
    if kind == StepKind.STALLED:
        return Severity.STALLED
    return Severity.STALLED  # unknown kind treated as lowest urgency


def _index_by_id(sessions: list[SessionDict]) -> dict[str, SessionDict]:
    return {s["thread_id"]: s for s in sessions}


def _is_stalled(session: SessionDict) -> bool:
    """Return True if the session has not produced an event for >= STALL_THRESHOLD.

    Uses session["age"] (pre-computed by build_fleet_state as now - last_event_at)
    rather than re-computing against real time, so the check is consistent with
    the snapshot that produced the age value.

    If no age is available (no last_event_at in build_fleet_state) the age field
    will be the time since created_at, which is conservative — still usable.
    A session with age == timedelta(0) is never considered stalled.
    """
    age: timedelta | None = session.get("age")
    if age is None:
        return False
    return age >= STALL_THRESHOLD


def _classify_against_prior(
    session: SessionDict,
    prior: SessionDict | None,
) -> MajorStep | None:
    """Classify a single current session against its prior snapshot, if any.

    Returns a MajorStep when a trigger fires, otherwise None.  At most one
    trigger is evaluated per session, in priority order:
    STARTED > FINISHED > NEEDS_INPUT > STALLED.
    """
    tid = session["thread_id"]
    status = session["status"]

    if prior is None:
        return MajorStep(
            thread_id=tid,
            label=session["label"],
            kind=StepKind.STARTED,
            severity=Severity.STARTED,
        )

    prior_status = prior["status"]

    if status in _TERMINAL_STATUSES and prior_status not in _TERMINAL_STATUSES:
        return MajorStep(
            thread_id=tid,
            label=session["label"],
            kind=StepKind.FINISHED,
            severity=_severity_for(StepKind.FINISHED, status),
        )

    if status == "waiting_approval" and prior_status != "waiting_approval":
        return MajorStep(
            thread_id=tid,
            label=session["label"],
            kind=StepKind.NEEDS_INPUT,
            severity=Severity.NEEDS_INPUT,
        )

    if status not in _TERMINAL_STATUSES and status != "waiting_approval":
        prev_ts = prior.get("last_event_at")
        curr_ts = session.get("last_event_at")
        if prev_ts == curr_ts and _is_stalled(session):
            return MajorStep(
                thread_id=tid,
                label=session["label"],
                kind=StepKind.STALLED,
                severity=Severity.STALLED,
            )

    return None


def _finished_by_disappearance(
    prev: list[SessionDict],
    curr: list[SessionDict],
) -> list[MajorStep]:
    """Detect sessions that finished by disappearing between snapshots."""
    curr_ids = {s["thread_id"] for s in curr}
    steps: list[MajorStep] = []

    for session in prev:
        if session["thread_id"] in curr_ids:
            continue
        if session["status"] in _TERMINAL_STATUSES:
            continue
        steps.append(
            MajorStep(
                thread_id=session["thread_id"],
                label=session["label"],
                kind=StepKind.FINISHED,
                severity=_severity_for(StepKind.FINISHED, session["status"]),
            )
        )

    return steps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_major_steps(
    prev: list[SessionDict],
    curr: list[SessionDict],
) -> list[MajorStep]:
    """Diff two fleet-state snapshots and return the list of major steps.

    Trigger rules (all structural — no content inspection):

      started      — thread_id appears in curr but NOT in prev
      finished     — thread_id in both; curr status is terminal (completed/failed/stopped)
                     and prev status was not terminal
      needs_input  — thread_id in both; curr status == "waiting_approval"
                     and prev status != "waiting_approval"
      stalled      — thread_id in curr (non-terminal); last_event_at has not changed
                     since prev AND silence >= STALL_THRESHOLD

    S0.2 guarantee: the function accesses only thread_id, status, and
    last_event_at from each SessionDict.  The label field is copied into the
    MajorStep for display but plays no role in trigger logic.  Callers must
    redact labels before outbound use (push.py does this).
    """
    prev_by_id = _index_by_id(prev)
    steps: list[MajorStep] = []

    for session in curr:
        step = _classify_against_prior(session, prev_by_id.get(session["thread_id"]))
        if step is not None:
            steps.append(step)

    steps.extend(_finished_by_disappearance(prev, curr))

    return steps


def severity_key(step: MajorStep) -> int:
    """Return the severity integer for a MajorStep — for use in sort(key=...).

    Higher value = higher urgency (matches Severity enum ordering).
    """
    return step["severity"]
