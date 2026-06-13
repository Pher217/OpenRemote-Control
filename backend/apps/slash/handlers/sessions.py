"""Fleet View F1 — /sessions command handler.

Queries active threads, renders a grouped fleet list as Telegram HTML, and
refreshes the pinned fleet dashboard.

Auth: operator-only.  Non-allowlisted callers receive a silent drop (the
handler returns None; the dispatcher ignores it).  The handler is intentionally
NOT async — it follows the same sync signature as all other slash handlers.
The Telegram send + dashboard refresh are done by the async caller (service.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from apps.threads.models import Thread

# Terminal statuses — sessions in these states are excluded from the fleet view.
_TERMINAL = frozenset(
    [
        Thread.StatusChoices.COMPLETED,
        Thread.StatusChoices.FAILED,
        Thread.StatusChoices.STOPPED,
    ]
)

# Active statuses (non-terminal).
_ACTIVE = frozenset(
    [
        Thread.StatusChoices.PENDING,
        Thread.StatusChoices.STARTING,
        Thread.StatusChoices.RUNNING,
        Thread.StatusChoices.WAITING_APPROVAL,
    ]
)

# Human-readable badge per runtime_mode.
_RUNTIME_BADGES: dict[str, str] = {
    Thread.RuntimeModeChoices.PTY: "Claude (PTY)",
    Thread.RuntimeModeChoices.RC: "Claude (RC)",
    Thread.RuntimeModeChoices.EXEC: "Claude (exec)",
    Thread.RuntimeModeChoices.API: "Claude (API)",
    Thread.RuntimeModeChoices.SDK: "Claude (SDK)",
    Thread.RuntimeModeChoices.OBSERVED: "Claude (observed)",
    Thread.RuntimeModeChoices.OPENCLAW: "OpenClaw",
    Thread.RuntimeModeChoices.HERMES: "Hermes",
}


def _runtime_badge(thread: Thread) -> str:
    return _RUNTIME_BADGES.get(thread.runtime_mode, thread.runtime_mode)


def _age_str(dt: datetime | None, now: datetime) -> str:
    """Format elapsed time as 'Xh Ym' or 'Ym' or 'Xs'."""
    if dt is None:
        return "?"
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0s"
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours}h {remaining_minutes}m"


def _needs_input(thread: Thread) -> bool:
    """Return True when this session is waiting for operator input.

    Heuristic: status is waiting_approval.  (A finer-grained check —
    inspecting pending Prompt rows — is deliberately out of scope for F1;
    the waiting_approval status is the canonical signal.)
    """
    return thread.status == Thread.StatusChoices.WAITING_APPROVAL


def _topic_link(thread: Thread) -> str | None:
    """Return a Telegram deep-link to this session's topic, or None."""
    topic_id = thread.metadata.get("telegram_topic_id")
    forum_chat_id = thread.metadata.get("telegram_forum_chat_id")
    if not topic_id or not forum_chat_id:
        return None
    # Telegram deep-link format for a forum topic:
    # https://t.me/c/<abs_forum_chat_id>/<topic_id>
    # forum_chat_id is negative (e.g. -1001234567890), strip the leading -100.
    abs_id = str(forum_chat_id).lstrip("-")
    if abs_id.startswith("100"):
        abs_id = abs_id[3:]
    return f"https://t.me/c/{abs_id}/{topic_id}"


def _esc(text: str) -> str:
    """Minimal HTML escape for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_thread_line(thread: Thread, now: datetime) -> str:
    """Render a single thread as one HTML line."""
    badge = _runtime_badge(thread)
    project = thread.project.name if thread.project else (thread.name or "—")
    host = thread.host.name if thread.host else "local"
    status = thread.status.replace("_", " ")
    age = _age_str(thread.started_at, now)
    idle = _age_str(thread.last_event_at, now)

    link = _topic_link(thread)
    if link:
        title = f'<a href="{link}">{_esc(project)}</a>'
    else:
        title = f"<b>{_esc(project)}</b>"

    return (
        f"• [{_esc(badge)}] {title} · {_esc(host)}"
        f" · {_esc(status)} · age {_esc(age)} · idle {_esc(idle)}"
    )


def render_fleet(threads: list[Thread], now: datetime) -> str:
    """Render the full fleet list as Telegram HTML.

    Groups:
      1. Needs input   — waiting_approval
      2. Working       — running / starting / pending
      3. Other         — anything else active

    Returns a plain "No active sessions." string when the list is empty.
    """
    if not threads:
        return "No active sessions."

    needs_input = [t for t in threads if _needs_input(t)]
    working = [
        t
        for t in threads
        if not _needs_input(t)
        and t.status
        in (
            Thread.StatusChoices.RUNNING,
            Thread.StatusChoices.STARTING,
            Thread.StatusChoices.PENDING,
        )
    ]
    other = [t for t in threads if t not in needs_input and t not in working]

    parts: list[str] = []
    parts.append(f"<b>Fleet ({len(threads)} active)</b>")

    if needs_input:
        parts.append("")
        parts.append("🔴 <b>Needs input</b>")
        for t in needs_input:
            parts.append(_format_thread_line(t, now))

    if working:
        parts.append("")
        parts.append("🟢 <b>Working</b>")
        for t in working:
            parts.append(_format_thread_line(t, now))

    if other:
        parts.append("")
        parts.append("⚪ <b>Idle / other</b>")
        for t in other:
            parts.append(_format_thread_line(t, now))

    return "\n".join(parts)


def _active_threads() -> list[Thread]:
    """Return non-terminal threads ordered by status priority then last_event_at."""
    return list(
        Thread.objects.select_related("project", "host")
        .exclude(status__in=list(_TERMINAL))
        .order_by("status", "-last_event_at")
    )


def handle(thread: Thread, args: list[str], *, from_user_id: int | None = None) -> dict:
    """Slash handler for /sessions.

    This handler is synchronous (follows the existing handler contract).
    Auth is checked here so the caller can pass from_user_id; if it is not
    supplied (legacy callers) the auth check is skipped.

    Returns a dict with:
      ok    — True on success
      text  — the Telegram HTML to send (parse_mode=HTML)
      refresh_dashboard — True to signal the caller to also refresh the dashboard
    """
    if from_user_id is not None and from_user_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        return {"ok": False, "drop": True}

    threads = _active_threads()
    now = datetime.now(tz=timezone.utc)
    text = render_fleet(threads, now)
    return {"ok": True, "text": text, "refresh_dashboard": True}
