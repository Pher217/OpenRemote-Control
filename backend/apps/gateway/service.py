"""Inbound message handling and outbox delivery service for the gateway app.

Routes inbound messages into threads, enqueues outbound replies into the
gateway outbox, and lets the messaging connector sidecar claim them.
"""
import logging

from asgiref.sync import async_to_sync
from django.utils import timezone

from apps.accounts.models import Account
from apps.threads.dispatch import dispatch_text
from apps.threads.models import Thread

logger = logging.getLogger(__name__)


def get_or_create_thread_for_chat(platform: str, chat_id: str) -> Thread:
    """Return the Thread bound to this (platform, chat_id), creating one if absent."""
    from apps.gateway.models import GatewayChat

    existing = (
        GatewayChat.objects.select_related("thread", "thread__account")
        .filter(platform=platform, chat_id=chat_id)
        .first()
    )
    if existing is not None:
        return existing.thread

    account, _ = Account.objects.get_or_create(
        provider=platform,
        label="gateway",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    thread = Thread.objects.create(
        name=f"gateway:{platform}:{chat_id}",
        runtime=platform,
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )
    GatewayChat.objects.create(platform=platform, chat_id=chat_id, thread=thread)
    return thread


def enqueue_text(
    platform: str,
    recipient: str,
    text: str,
    prompt_nonce: str = "",
) -> None:
    """Persist a GatewayMessage for outbound delivery by the Node sidecar."""
    from apps.gateway.models import GatewayMessage

    GatewayMessage.objects.create(
        platform=platform,
        recipient=recipient,
        text=text,
        prompt_nonce=prompt_nonce,
    )


def enqueue_prompt(platform: str, recipient: str, prompt) -> None:
    """Render a Prompt as numbered text and enqueue it for the given recipient."""
    from apps.prompts.render import render_prompt

    enqueue_text(platform, recipient, render_prompt(prompt), prompt.nonce)


def handle_inbound(platform: str, chat_id: str, sender: str, text: str) -> str | None:
    """Process an inbound message and return a reply string, or None.

    Logic:
    1. Get-or-create the Thread for (platform, chat_id).
    2. Find the latest PENDING Prompt on that thread.
    3. If found, try parse_reply; on match resolve and return "Recorded ✔"
       (or "Expired/Invalid" when resolve returns None).
    4. If no pending prompt, dispatch_text and capture the assistant reply.
    """
    from apps.prompts.models import Prompt
    from apps.prompts.render import parse_reply
    from apps.prompts.service import resolve as resolve_prompt

    try:
        thread = get_or_create_thread_for_chat(platform, chat_id)
    except Exception:
        logger.exception("gateway handle_inbound: could not get/create thread")
        return None

    # Step 1: look for a PENDING prompt on this thread.
    pending = (
        Prompt.objects.filter(
            thread=thread,
            status=Prompt.StatusChoices.PENDING,
        )
        .order_by("-requested_at")
        .first()
    )

    if pending is not None:
        try:
            kwargs = parse_reply(pending, text)
        except Exception:
            kwargs = None

        if kwargs is not None:
            resolved = resolve_prompt(pending.nonce, by=sender, **kwargs)
            if resolved is not None:
                return "Recorded ✔"
            return "Expired/Invalid"
        # Reply does not map to the prompt's options — fall through to dispatch.

    # Step 2: no pending prompt (or reply didn't parse) — dispatch as free text.
    reply_holder: list[str] = []

    async def _run_dispatch():
        async def on_event(data):
            etype = data.get("type")
            if etype == "message_complete":
                reply_holder.append(data.get("text") or "")
            elif etype == "slash_result":
                reply_holder.append(data.get("message", ""))
            elif etype == "error":
                reply_holder.append(f"⚠️ {data.get('message', '')}")

        await dispatch_text(thread, text, on_event=on_event)

    try:
        async_to_sync(_run_dispatch)()
    except Exception:
        logger.exception("gateway handle_inbound: dispatch_text failed")
        return None

    return reply_holder[0] if reply_holder else None


def claim_outbox(platform: str, max_count: int) -> list[dict]:
    """Fetch up to max_count undelivered messages for platform, mark them delivered.

    Returns a list of dicts: [{id, platform, recipient, text}].
    """
    from django.db import transaction

    from apps.gateway.models import GatewayMessage

    now = timezone.now()

    with transaction.atomic():
        rows = list(
            GatewayMessage.objects.select_for_update()
            .filter(platform=platform, delivered_at__isnull=True)
            .order_by("created_at")[: max_count]
        )
        if rows:
            ids = [r.id for r in rows]
            GatewayMessage.objects.filter(id__in=ids).update(delivered_at=now)

    return [
        {
            "id": r.id,
            "platform": r.platform,
            "recipient": r.recipient,
            "text": r.text,
        }
        for r in rows
    ]
