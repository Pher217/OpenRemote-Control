import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Max

from apps.accounts.models import Account
from apps.connectors.models import ConnectorInstance
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt, get_by_nonce
from apps.prompts.surfaces.telegram import build_reply_markup
from apps.threads.models import Message, Thread

logger = logging.getLogger(__name__)


def register_or_touch(
    connector_id: str,
    tool: str,
    workspace_root: str,
) -> tuple[ConnectorInstance, Thread]:
    """Get-or-create the ConnectorInstance and its bound Thread.

    One thread per connector_id. The Account is keyed on provider=tool /
    label='connector' so multiple connectors using the same tool share one
    account row (sufficient for v1; per-connector accounts can come later).
    """
    account, _ = Account.objects.get_or_create(
        provider=tool or "connector",
        label="connector",
        defaults={"auth_type": "none", "credential_type": "none"},
    )

    instance = ConnectorInstance.objects.filter(connector_id=connector_id).first()

    if instance is None:
        thread = Thread.objects.create(
            name=f"connector:{connector_id}",
            runtime=tool or "connector",
            runtime_mode=Thread.RuntimeModeChoices.API,
            account=account,
        )
        instance = ConnectorInstance.objects.create(
            connector_id=connector_id,
            tool=tool,
            workspace_root=workspace_root,
            thread=thread,
        )
    else:
        # Update mutable fields; last_seen_at updates automatically (auto_now).
        changed = []
        if instance.workspace_root != workspace_root:
            instance.workspace_root = workspace_root
            changed.append("workspace_root")
        if changed:
            instance.save(update_fields=[*changed, "last_seen_at"])
        else:
            instance.save(update_fields=["last_seen_at"])

        thread = instance.thread
        if thread is None:
            thread = Thread.objects.create(
                name=f"connector:{connector_id}",
                runtime=tool or "connector",
                runtime_mode=Thread.RuntimeModeChoices.API,
                account=account,
            )
            instance.thread = thread
            instance.save(update_fields=["thread", "last_seen_at"])

    return instance, thread


def _next_sequence(thread: Thread) -> int:
    nxt = (
        Message.objects.filter(thread=thread).aggregate(m=Max("sequence"))["m"] or 0
    ) + 1
    return nxt


def notify(
    connector_id: str,
    tool: str,
    workspace_root: str,
    message: str,
) -> None:
    _, thread = register_or_touch(connector_id, tool, workspace_root)

    Message.objects.create(
        thread=thread,
        role=Message.RoleChoices.SYSTEM_EVENT,
        redacted_content=message,
        sequence=_next_sequence(thread),
    )

    chat_id_str = getattr(settings, "ORC_PROMPT_CHAT_ID", "")
    if chat_id_str:
        try:
            from apps.telegram.telegram_api import send_message

            async_to_sync(send_message)(int(chat_id_str), message)
        except Exception:
            logger.exception("connector notify: telegram delivery failed (best-effort)")


def ask(
    connector_id: str,
    tool: str,
    workspace_root: str,
    question: str,
    options: list[str],
) -> str:
    """Create a CHOICE_SINGLE or FREE_TEXT prompt and return its nonce."""
    _, thread = register_or_touch(connector_id, tool, workspace_root)

    if options:
        prompt_type = Prompt.PromptType.CHOICE_SINGLE
        prompt_options = [{"key": o[:40], "label": o} for o in options]
    else:
        prompt_type = Prompt.PromptType.FREE_TEXT
        prompt_options = []

    prompt = create_prompt(
        thread,
        prompt_type=prompt_type,
        question=question,
        options=prompt_options,
        trust_class=Prompt.TrustClass.DECISION,
        ttl_seconds=900,
    )

    _deliver(prompt)
    return prompt.nonce


def approve(
    connector_id: str,
    tool: str,
    workspace_root: str,
    action: str,
    preview: str,
) -> str:
    """Create an APPROVAL prompt and return its nonce."""
    _, thread = register_or_touch(connector_id, tool, workspace_root)

    prompt = create_prompt(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question=action,
        body=preview,
        options=[
            {"key": "allow", "label": "Allow"},
            {"key": "deny", "label": "Deny"},
        ],
        trust_class=Prompt.TrustClass.APPROVAL,
        ttl_seconds=900,
    )

    _deliver(prompt)
    return prompt.nonce


def result(nonce: str) -> dict:
    """Return the current status dict for a prompt nonce."""
    prompt = get_by_nonce(nonce)

    if prompt is None:
        return {"status": "expired"}

    status = prompt.status

    if status != Prompt.StatusChoices.ANSWERED:
        return {"status": status}

    response = prompt.response or {}

    # APPROVAL prompt: map option key -> decision field
    if prompt.prompt_type == Prompt.PromptType.APPROVAL:
        option_keys = response.get("option_keys", [])
        decision = option_keys[0] if option_keys else "deny"
        return {"status": status, "decision": decision}

    # CHOICE_SINGLE / FREE_TEXT: return answer field
    if "text" in response:
        answer = response["text"]
    else:
        option_keys = response.get("option_keys", [])
        if option_keys:
            key = option_keys[0]
            # Try to find the label from the stored options
            label_map = {opt["key"]: opt.get("label", key) for opt in (prompt.options or [])}
            answer = label_map.get(key, key)
        else:
            answer = ""

    return {"status": status, "answer": answer}


def _deliver(prompt: Prompt) -> None:
    """Best-effort Telegram delivery of a prompt. Never raises."""
    chat_id_str = getattr(settings, "ORC_PROMPT_CHAT_ID", "")
    if not chat_id_str:
        return

    try:
        from apps.telegram.telegram_api import send_message

        reply_markup = build_reply_markup(prompt)
        text = prompt.question
        if prompt.body:
            text = f"{text}\n\n{prompt.body}"

        async_to_sync(send_message)(
            int(chat_id_str),
            text,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("connector deliver: telegram delivery failed (best-effort)")
