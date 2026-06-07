from channels.db import database_sync_to_async
from django.conf import settings

from apps.messaging import routing
from apps.observe.formatting import _esc, format_turn
from apps.telegram import telegram_api
from apps.telegram.telegram_api import FORUM_ICON_COLORS

TELEGRAM_MAX = 4096


def pick_color(session_id: str) -> int:
    return FORUM_ICON_COLORS[sum(ord(c) for c in session_id) % len(FORUM_ICON_COLORS)]


def _topic_name(thread) -> str:
    prov = thread.metadata.get("provider") or thread.runtime
    repo = thread.metadata.get("repo") or "?"
    title = thread.metadata.get("title") or thread.external_session_ref[:8]
    return f"{prov} · {repo} · {title}"[:128]


@database_sync_to_async
def _ensure_topic_id(thread, forum_chat_id) -> tuple[int | None, str, int]:
    existing = thread.metadata.get("telegram_topic_id")
    topic_name = _topic_name(thread)
    color = pick_color(thread.external_session_ref)
    return existing, topic_name, color


@database_sync_to_async
def _save_topic_id(thread, topic_id, color, forum_chat_id) -> None:
    thread.metadata["telegram_topic_id"] = topic_id
    thread.metadata["telegram_icon_color"] = color
    thread.metadata["telegram_forum_chat_id"] = forum_chat_id
    thread.save(update_fields=["metadata"])


async def deliver_turn(thread, parsed, msg, *, forum_chat_id, api=None) -> None:
    if api is None:
        api = telegram_api

    existing, name, color = await _ensure_topic_id(thread, forum_chat_id)
    if existing is None:
        topic_id = await api.create_forum_topic(forum_chat_id, name, color)
        await _save_topic_id(thread, topic_id, color, forum_chat_id)
        prov = thread.metadata.get("provider") or thread.runtime
        repo = thread.metadata.get("repo") or "?"
        branch = thread.metadata.get("branch") or ""
        title = thread.metadata.get("title") or thread.external_session_ref[:8]
        intro = (
            f"<b>{_esc(prov)}</b> · <code>{_esc(repo)}</code>"
            f" · branch <code>{_esc(branch or '—')}</code>\n"
            f"<b>{_esc(title)}</b>\n"
            f"session <code>{_esc(thread.external_session_ref)}</code>"
        )
        await api.send_message(
            forum_chat_id, intro, message_thread_id=topic_id, parse_mode="HTML"
        )
    else:
        topic_id = existing

    html = format_turn(
        parsed,
        user_label=settings.TELEGRAM_USER_LABEL,
        assistant_label=settings.TELEGRAM_ASSISTANT_LABEL,
    )
    try:
        await api.send_message(
            forum_chat_id,
            html,
            message_thread_id=topic_id,
            parse_mode="HTML",
            disable_notification=True,
        )
    except Exception:
        label = (
            settings.TELEGRAM_USER_LABEL
            if parsed["role"] == "user"
            else settings.TELEGRAM_ASSISTANT_LABEL
        )
        plain = f"{label}: {parsed['text'][:3900]}"
        await api.send_message(
            forum_chat_id,
            plain,
            message_thread_id=topic_id,
            disable_notification=True,
        )


async def deliver_turn_active(thread, parsed, msg, *, api=None) -> None:
    """Deliver a turn to whichever messaging platform is currently active.

    Returns immediately (no-op) when no recipient is configured.
    Gateway platforms receive a plain-text message prefixed with a session label.
    Never raises — all exceptions are swallowed.
    """
    recipient = routing.active_recipient()
    if not recipient:
        return

    try:
        if routing.is_telegram():
            await deliver_turn(thread, parsed, msg, forum_chat_id=int(recipient), api=api)
        else:
            label = _topic_name(thread)
            role = parsed["role"]
            role_label = (
                settings.TELEGRAM_USER_LABEL
                if role == "user"
                else settings.TELEGRAM_ASSISTANT_LABEL
            )
            text = f"[{label}] {role_label}: {parsed['text'][:3900]}"
            platform = routing.active_platform()
            from apps.gateway.service import enqueue_text  # noqa: PLC0415

            await database_sync_to_async(enqueue_text)(platform, recipient, text)
    except Exception:  # noqa: BLE001
        pass
