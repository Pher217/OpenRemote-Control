"""
build_fleet_state() — read-only ORM snapshot of active sessions.

Returns a list of dicts, one per active Thread (non-terminal status).
All values are plain Python scalars — no ORM objects leak out of this
module so callers (digest, tests) can operate without a DB connection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from apps.threads.models import Thread

# Terminal statuses — sessions in these states are excluded.
_TERMINAL = frozenset(
    [
        Thread.StatusChoices.COMPLETED,
        Thread.StatusChoices.FAILED,
        Thread.StatusChoices.STOPPED,
    ]
)


class SessionDict(TypedDict):
    thread_id: str
    label: str
    runtime_mode: str
    host: str
    status: str
    last_event_at: datetime | None
    age: timedelta
    needs_input: bool


def _needs_input(thread: Thread) -> bool:
    """Return True when this session is waiting for operator input.

    Reuses the same heuristic as apps/slash/handlers/sessions.py:74 —
    status == WAITING_APPROVAL is the canonical signal (F1 ADR).
    """
    return thread.status == Thread.StatusChoices.WAITING_APPROVAL


def build_fleet_state() -> list[SessionDict]:
    """Query non-terminal threads and return a plain-dict snapshot.

    Each dict contains:
      thread_id     — UUID str
      label         — thread.name (project name or runtime label)
      runtime_mode  — e.g. "pty", "observed", "controlled"
      host          — host name or "local"
      status        — e.g. "running", "waiting_approval"
      last_event_at — datetime or None
      age           — timedelta since last_event_at (or created_at)
      needs_input   — bool (True iff status == waiting_approval)
    """
    now = datetime.now(tz=timezone.utc)
    threads = (
        Thread.objects.select_related("host")
        .exclude(status__in=list(_TERMINAL))
        # Exclude Telegram chat surfaces (the operator's own DM/forum conversation
        # with the bot has a TelegramChat row) — those are not coding sessions and
        # must not appear in the fleet a session is reported to.
        .filter(telegram_chat__isnull=True)
        .order_by("status", "-last_event_at")
    )
    result: list[SessionDict] = []
    for t in threads:
        anchor = t.last_event_at or t.created_at or now
        age = now - anchor
        result.append(
            {
                "thread_id": str(t.id),
                "label": t.name,
                "runtime_mode": t.runtime_mode,
                "host": t.host.name if t.host else "local",
                "status": t.status,
                "last_event_at": t.last_event_at,
                "age": age,
                "needs_input": _needs_input(t),
            }
        )
    return result


def group_fleet_state(sessions: list[SessionDict]) -> dict[str, list[SessionDict]]:
    """Partition sessions into the three canonical display groups.

    Groups:
      needs_input — waiting_approval (requires operator action)
      working     — running / starting / pending
      idle        — everything else active (should be rare)

    Ordering within each group preserves the input order.
    """
    needs_input: list[SessionDict] = []
    working: list[SessionDict] = []
    idle: list[SessionDict] = []

    working_statuses = frozenset(
        [
            Thread.StatusChoices.RUNNING,
            Thread.StatusChoices.STARTING,
            Thread.StatusChoices.PENDING,
        ]
    )

    for s in sessions:
        if s["needs_input"]:
            needs_input.append(s)
        elif s["status"] in working_statuses:
            working.append(s)
        else:
            idle.append(s)

    return {"needs_input": needs_input, "working": working, "idle": idle}
