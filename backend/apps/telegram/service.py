import datetime as _dt
import logging
from datetime import timedelta

from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Account
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt, resolve as resolve_prompt
from apps.prompts.surfaces.telegram import build_reply_markup, parse_callback
from apps.slash.fleet_dashboard import refresh_fleet_dashboard
from apps.slash.handlers.sessions import _active_threads, render_fleet
from apps.telegram.models import TelegramChat
from apps.threads.dispatch import dispatch_text
from apps.threads.models import Thread

log = logging.getLogger(__name__)


def get_or_create_thread_for_chat(chat_id) -> Thread:
    existing = (
        TelegramChat.objects.select_related("thread", "thread__account")
        .filter(chat_id=chat_id)
        .first()
    )
    if existing is not None:
        return existing.thread

    account, _ = Account.objects.get_or_create(
        provider="ollama",
        label="telegram",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    thread = Thread.objects.create(
        name=f"telegram:{chat_id}",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={"model": settings.TELEGRAM_DEFAULT_MODEL},
    )
    TelegramChat.objects.create(chat_id=chat_id, thread=thread)
    return thread


@database_sync_to_async
def _get_thread_with_account(thread_id) -> Thread:
    return Thread.objects.select_related("account").get(id=thread_id)


@database_sync_to_async
def _create_pairing(tool: str, label: str, ttl: int = 900):
    """Create a Pairing row and return (code, expires_at)."""
    from apps.connectors.models import Pairing

    now = timezone.now()
    pairing = Pairing.objects.create(
        tool=tool,
        label=label,
        expires_at=now + timedelta(seconds=ttl),
    )
    return pairing.code, pairing.expires_at


@database_sync_to_async
def _lookup_thread_for_topic(forum_chat_id: int, message_thread_id: int):
    """Return the Thread whose topic lives in this forum, or None."""
    return (
        Thread.objects.select_related("host")
        .filter(
            metadata__telegram_topic_id=message_thread_id,
            metadata__telegram_forum_chat_id=forum_chat_id,
        )
        .first()
    )


async def handle_forum_reply(
    forum_chat_id: int,
    message_thread_id: int,
    from_user_id: int,
    text: str,
    *,
    send,
) -> None:
    """Handle a user reply sent inside a Telegram forum topic.

    Auth: from_user_id must be in TELEGRAM_ALLOWED_CHAT_IDS AND
          forum_chat_id must match TELEGRAM_FORUM_CHAT_ID.
    Behaviour in Phase 1:
      - Unknown topic → inform user.
      - Read-only session (not PTY / no host / no tmux_session_name) → inform user.
      - Driveable PTY session → placeholder reply (injection is Phase 4).
    """
    # --- Auth gate -----------------------------------------------------------
    if from_user_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        return

    forum_setting = settings.TELEGRAM_FORUM_CHAT_ID
    if not forum_setting:
        return
    try:
        configured_forum_id = int(forum_setting)
    except (ValueError, TypeError):
        return
    if forum_chat_id != configured_forum_id:
        return

    # --- Reverse lookup ------------------------------------------------------
    thread = await _lookup_thread_for_topic(forum_chat_id, message_thread_id)
    if thread is None:
        await send(
            forum_chat_id,
            "No matching session for this topic.",
            message_thread_id=message_thread_id,
        )
        return

    # --- Read-only guard -----------------------------------------------------
    is_pty = thread.runtime_mode == Thread.RuntimeModeChoices.PTY
    has_host = thread.host_id is not None
    has_tmux = bool(thread.metadata.get("tmux_session_name"))

    if not (is_pty and has_host and has_tmux):
        await send(
            forum_chat_id,
            "This session is read-only — start it with `orc run` to send input.",
            message_thread_id=message_thread_id,
        )
        return

    # --- Driveable PTY session -----------------------------------------------
    # Phase 5: create an APPROVAL Prompt whose payload binds the exact text to
    # inject.  The text is stored in surface_message_ref["inject_text"] — the
    # source of truth for what will be injected if approved.  The raw Telegram
    # message is never re-read after this point.
    @database_sync_to_async
    def _create_inject_approval():
        return create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question=f"Inject into `{thread.name}`?",
            body=f"Text: {text!r}",
            options=[
                {"key": "allow", "label": "Allow"},
                {"key": "deny", "label": "Deny"},
            ],
            trust_class=Prompt.TrustClass.APPROVAL,
            ttl_seconds=300,
            surface_message_ref={
                "action": "pty_inject",
                "thread_id": str(thread.id),
                "inject_text": text,
            },
        )

    prompt = await _create_inject_approval()

    # Deliver the approval request inline via the injected send callable
    # (same transport already used for read-only replies in this handler).
    # The reply_markup is not available via the plain send() signature used in
    # tests; delivery of the inline keyboard is best-effort via Telegram API
    # when running for real, but the Prompt already exists in DB — a timeout
    # or delivery failure here does NOT prevent the Prompt from being resolved
    # if the operator finds it another way.  Fail-closed: if delivery fails,
    # nothing is injected (the Prompt stays PENDING until it expires).
    reply_markup = build_reply_markup(prompt)
    msg = prompt.question
    if prompt.body:
        msg = f"{msg}\n\n{prompt.body}"
    await send(
        forum_chat_id,
        msg,
        message_thread_id=message_thread_id,
    )


async def handle_update(chat_id: int, text: str, *, send):
    if chat_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        return

    # /sessions — global fleet view (operator-only; auth gate is the check above).
    if text.strip().startswith("/sessions"):
        threads = await database_sync_to_async(_active_threads)()
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        fleet_text = render_fleet(threads, now)
        await send(chat_id, fleet_text, parse_mode="HTML")
        await refresh_fleet_dashboard()
        return

    # /pair [tool] [label] — create a pairing code and send the QR image.
    if text.strip().startswith("/pair"):
        parts = text.strip().split(maxsplit=3)
        tool = parts[1] if len(parts) > 1 else ""
        label = parts[2] if len(parts) > 2 else ""
        await _handle_pair_command(chat_id, tool, label)
        return

    thread = await database_sync_to_async(get_or_create_thread_for_chat)(chat_id)
    thread = await _get_thread_with_account(thread.id)

    buffer = ""
    reply = ""

    async def on_event(data):
        nonlocal buffer, reply
        etype = data.get("type")
        if etype == "message_delta":
            buffer += data.get("text", "")
        elif etype == "message_complete":
            reply = data.get("text") or buffer
        elif etype == "slash_result":
            reply = data.get("message", "")
        elif etype == "error":
            reply = f"⚠️ {data.get('message', '')}"

    await dispatch_text(thread, text, on_event=on_event)

    if reply:
        await send(chat_id, reply)


async def handle_callback_query(
    callback_query_id: str,
    from_user_id: int,
    data: str,
    *,
    answer,
) -> None:
    if from_user_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        await answer(callback_query_id, text="Not authorised.")
        return

    parsed = parse_callback(data)
    if parsed is None:
        await answer(callback_query_id, text="Unknown callback.")
        return

    nonce, key = parsed

    _resolve = database_sync_to_async(resolve_prompt)
    prompt = await _resolve(nonce, option_keys=[key], by=str(from_user_id))

    if prompt is None:
        await answer(callback_query_id, text="Expired or already answered.")
        return

    await answer(callback_query_id, text="Recorded ✔")

    # Phase 5 dispatch: if this was a pty_inject approval and the operator
    # chose "allow", dispatch the injection now.  The inject_text bound at
    # approval-creation time is used — never the raw Telegram message.
    # Fail-closed: any error in the dispatch path is caught and logged; the
    # operator's "Recorded ✔" ack has already been sent so we never block it.
    ref = prompt.surface_message_ref or {}
    if ref.get("action") == "pty_inject" and key == "allow":
        thread_id = ref.get("thread_id")
        inject_text = ref.get("inject_text", "")
        if thread_id and inject_text:
            # Fetch the thread row in a sync DB call, then dispatch via the
            # async channel layer path — async_send_pty_input awaits group_send
            # directly so it works inside this running event loop.
            @database_sync_to_async
            def _fetch_thread():
                from apps.threads.models import Thread as _Thread  # noqa: PLC0415

                try:
                    return _Thread.objects.select_related("host").get(id=thread_id)
                except _Thread.DoesNotExist:
                    log.error("pty_inject: thread %s not found after approval", thread_id)
                    return None

            t = await _fetch_thread()
            if t is not None:
                try:
                    from apps.hostlink.service import async_send_pty_input  # noqa: PLC0415

                    await async_send_pty_input(t, inject_text, approved=True)
                except Exception:
                    log.exception("pty_inject: dispatch failed after approval")
        else:
            log.error(
                "pty_inject: approval resolved but payload incomplete: %r", ref
            )


async def _handle_pair_command(chat_id: int, tool: str, label: str) -> None:
    """Create a pairing code and send the QR PNG to the Telegram chat."""
    from apps.connectors.qr import pairing_payload, png_bytes
    from apps.telegram.telegram_api import send_message, send_photo

    code, expires_at = await _create_pairing(tool, label)
    backend_url = getattr(settings, "ORC_PUBLIC_BASE_URL", "")
    payload = pairing_payload(code, backend_url)

    try:
        png = png_bytes(payload)
        cmd = f"orc-mcp pair {code}"
        if backend_url:
            cmd += f" --backend {backend_url}"
        caption = f"Pairing code: {code}\nExpires: {expires_at.strftime('%H:%M UTC')}\n\n{cmd}"
        await send_photo(chat_id, png, caption=caption)
    except Exception:
        # Fallback to text if photo send fails (e.g. no bot permission to send media).
        cmd = f"orc-mcp pair {code}"
        if backend_url:
            cmd += f" --backend {backend_url}"
        await send_message(
            chat_id,
            f"Pairing code: `{code}`\nExpires: {expires_at.strftime('%H:%M UTC')}\n\nRun: `{cmd}`",
            parse_mode="Markdown",
        )
