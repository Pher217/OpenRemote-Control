"""Deliver observed session turns to Telegram.

Sends a session-start intro into a per-session Telegram forum topic, edits an
in-place assistant digest, and recreates the topic when it has gone stale.
"""
import logging

import httpx
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings
from django.core.cache import cache

from apps.messaging import routing
from apps.observe.formatting import _esc, format_turn
from apps.telegram import telegram_api
from apps.telegram.telegram_api import FORUM_ICON_COLORS

log = logging.getLogger(__name__)

TELEGRAM_MAX = 4096
# Maximum characters shown in a digest excerpt before truncation.
_DIGEST_EXCERPT_MAX = 300

_cache_get = sync_to_async(cache.get)
_cache_set = sync_to_async(cache.set)
_cache_delete = sync_to_async(cache.delete)


def _topic_not_found(exc: httpx.HTTPStatusError) -> bool:
    """True if an httpx error is Telegram's 400 'message thread not found'."""
    resp = getattr(exc, "response", None)
    if resp is None or resp.status_code != 400:
        return False
    try:
        desc = (resp.json() or {}).get("description", "")
    except Exception:  # noqa: BLE001
        desc = getattr(resp, "text", "") or ""
    return "message thread not found" in desc.lower()


@database_sync_to_async
def _clear_topic_state(thread) -> None:
    for k in (
        "telegram_topic_id",
        "telegram_digest_message_id",
        "telegram_digest_steps",
        "telegram_icon_color",
    ):
        thread.metadata.pop(k, None)
    thread.save(update_fields=["metadata"])


def pick_color(session_id: str) -> int:
    return FORUM_ICON_COLORS[sum(ord(c) for c in session_id) % len(FORUM_ICON_COLORS)]


def _topic_name(thread) -> str:
    prov = thread.metadata.get("provider") or thread.runtime
    repo = thread.metadata.get("repo") or "?"
    title = thread.metadata.get("title") or thread.external_session_ref[:8]

    # Host prefix stored in metadata — avoids FK access in async contexts.
    host_name = (thread.metadata or {}).get("host_name")
    prefix = f"[{host_name}] " if host_name else ""

    # Mode marker: driveable (PTY/headless) vs read-only (observed JSONL).
    marker = "👁 " if getattr(thread, "runtime_mode", None) == "observed" else "✏️ "

    return f"{prefix}{marker}{prov} · {repo} · {title}"[:128]


def _truncate_digest(text: str) -> str:
    """Truncate text to _DIGEST_EXCERPT_MAX chars with a trailing ellipsis."""
    if len(text) <= _DIGEST_EXCERPT_MAX:
        return text
    return text[:_DIGEST_EXCERPT_MAX] + "…"


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


@database_sync_to_async
def _save_digest_state(thread, digest_message_id, digest_steps, digest_text=None) -> None:
    thread.metadata["telegram_digest_message_id"] = digest_message_id
    thread.metadata["telegram_digest_steps"] = digest_steps
    if digest_text is not None:
        thread.metadata["telegram_digest_text"] = digest_text
    thread.save(update_fields=["metadata"])


@database_sync_to_async
def _clear_digest_state(thread) -> None:
    thread.metadata.pop("telegram_digest_message_id", None)
    thread.metadata.pop("telegram_digest_steps", None)
    thread.metadata.pop("telegram_digest_text", None)
    thread.save(update_fields=["metadata"])


async def deliver_turn(thread, parsed, msg, *, forum_chat_id, api=None) -> None:
    try:
        await _deliver_turn_once(thread, parsed, msg, forum_chat_id=forum_chat_id, api=api)
    except httpx.HTTPStatusError as exc:
        if not _topic_not_found(exc):
            raise
        log.warning(
            "telegram topic stale for thread %s; clearing and recreating", thread.id
        )
        await _clear_topic_state(thread)
        turn_uuid = parsed.get("uuid")
        if turn_uuid:
            await _cache_delete(f"observe:deliver:{thread.id}:{turn_uuid}")
        # Retry once: topic_id is now cleared so _ensure_topic_id creates a fresh topic.
        await _deliver_turn_once(thread, parsed, msg, forum_chat_id=forum_chat_id, api=api)


async def _deliver_turn_once(thread, parsed, msg, *, forum_chat_id, api=None) -> None:
    if api is None:
        api = telegram_api

    # Best-effort dedup against a redelivered turn (e.g. the daemon re-sends a
    # queued reply after a reconnect). Key on the STABLE per-turn uuid when the
    # payload carries one; record_turn is NOT idempotent (it creates a distinct
    # Message each call), so msg.id cannot be the key. Headless drive replies
    # carry no uuid, so dedup is simply skipped for them.
    turn_uuid = parsed.get("uuid")
    if turn_uuid:
        cache_key = f"observe:deliver:{thread.id}:{turn_uuid}"
        if await _cache_get(cache_key):
            return
        await _cache_set(cache_key, True, timeout=30)

    mode = getattr(settings, "OBSERVE_DELIVERY_MODE", "progress")
    role = parsed["role"]

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
        # Session-start intro always notifies (disable_notification omitted → default notify).
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

    if role == "user":
        # Milestone: post a fresh notifying message, then freeze any active digest so
        # the next assistant turn starts a new digest thread.
        await _clear_digest_state(thread)
        try:
            await api.send_message(
                forum_chat_id,
                html,
                message_thread_id=topic_id,
                parse_mode="HTML",
                disable_notification=False,
            )
        except Exception:
            log.debug("user HTML send failed, falling back to plain text", exc_info=True)
            label = settings.TELEGRAM_USER_LABEL
            plain = f"{label}: {parsed['text'][:3900]}"
            await api.send_message(
                forum_chat_id,
                plain,
                message_thread_id=topic_id,
                disable_notification=False,
            )
        return

    # role == "assistant"
    if mode == "milestones_only":
        return

    if mode == "all":
        try:
            await api.send_message(
                forum_chat_id,
                html,
                message_thread_id=topic_id,
                parse_mode="HTML",
                disable_notification=True,
            )
        except Exception:
            log.debug("assistant HTML send failed (all mode), falling back to plain text", exc_info=True)
            label = settings.TELEGRAM_ASSISTANT_LABEL
            plain = f"{label}: {parsed['text'][:3900]}"
            await api.send_message(
                forum_chat_id,
                plain,
                message_thread_id=topic_id,
                disable_notification=True,
            )
        return

    # mode == "progress": maintain a per-thread digest message edited in place.
    # A turn's first streamed step carries reset → start a fresh digest so each
    # turn is one edited message (handled here, in delivery order, so a fast next
    # turn can't clear the digest before the prior turn's queued chunks drained).
    if parsed.get("reset"):
        await _clear_digest_state(thread)
    digest_msg_id = thread.metadata.get("telegram_digest_message_id")
    digest_steps = thread.metadata.get("telegram_digest_steps", 0)
    acc = thread.metadata.get("telegram_digest_text", "")

    # Accumulate each streamed step so the operator watches the work BUILD UP — a
    # growing transcript (🔧 tool steps then the answer) — instead of the message
    # flashing to only the latest step. Plain text (no HTML) keeps the accreting
    # transcript escaping-safe.
    label = settings.TELEGRAM_ASSISTANT_LABEL
    step_line = _truncate_digest(parsed["text"])
    acc = f"{acc}\n{step_line}" if acc else step_line
    digest_steps += 1
    # Stay under Telegram's 4096-char message limit — keep the most recent tail.
    shown = acc if len(acc) <= 3800 else "…\n" + acc[-3800:]
    body = f"{label}:\n{shown}"

    if digest_msg_id is None:
        # No active digest — send a new silent message and store its id + transcript.
        try:
            new_id = await api.send_message(
                forum_chat_id, body, message_thread_id=topic_id, disable_notification=True,
            )
        except Exception:
            log.debug("new digest send failed", exc_info=True)
            return
        await _save_digest_state(thread, new_id, digest_steps, acc)
    else:
        # Active digest — edit in place (no re-notification) with the grown transcript.
        edited = await api.edit_message_text(
            forum_chat_id, digest_msg_id, body, message_thread_id=topic_id,
        )
        if not edited:
            # Edit failed (message too old / deleted) — start a fresh digest.
            try:
                new_id = await api.send_message(
                    forum_chat_id, body, message_thread_id=topic_id, disable_notification=True,
                )
            except Exception:
                log.debug("fresh digest send failed after stale edit", exc_info=True)
                return
            await _save_digest_state(thread, new_id, digest_steps, acc)
        else:
            await _save_digest_state(thread, digest_msg_id, digest_steps, acc)


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
        log.exception("deliver_turn_active failed for thread %s", getattr(thread, "id", "?"))
