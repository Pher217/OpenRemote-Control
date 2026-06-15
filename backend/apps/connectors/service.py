"""Service layer for the connectors MCP bridge.

Routes messages, questions, approvals, and session lifecycle events
between coding-agent sessions and the operator's connector chat surfaces.
Best-effort delivery is attempted to the active messaging platform.
"""

import logging

from asgiref.sync import async_to_sync
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import Account
from apps.connectors.models import ConnectorInstance
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt, get_by_nonce
from apps.prompts.surfaces.telegram import build_reply_markup
from apps.threads.models import Message, Thread

logger = logging.getLogger(__name__)


def resolve_connector_id(request, body_connector_id: str) -> str:
    """Return the authoritative connector_id for this request.

    When the request is signature-authenticated, the connector_id on the
    registered ConnectorKey is server-authoritative — we ignore the body value
    to prevent identity spoofing via a crafted connector_id field.
    For legacy token-authenticated requests the body value is trusted as-is.
    """
    from apps.connectors.auth import ConnectorSignatureAuthentication
    from apps.connectors.models import ConnectorKey

    if isinstance(request.successful_authenticator, ConnectorSignatureAuthentication):
        key = request.user  # set by ConnectorSignatureAuthentication as principal
        if isinstance(key, ConnectorKey):
            return key.connector_id
    return body_connector_id


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


def _broadcast_text(text: str) -> None:
    """Send a plain-text message to the single active platform (best-effort)."""
    from apps.messaging import routing

    recipient = routing.active_recipient()
    if not recipient:
        return
    try:
        if routing.is_telegram():
            from apps.telegram.telegram_api import send_message

            async_to_sync(send_message)(int(recipient), text)
        else:
            from apps.gateway.service import enqueue_text

            enqueue_text(routing.active_platform(), recipient, text)
    except Exception:
        logger.exception("connector broadcast: delivery failed (best-effort)")

def start_session(
    connector_id: str,
    tool: str,
    workspace_root: str,
    name: str,
) -> dict:
    """Start a new remote-control session and dispatch it to the operator's chat.

    This is the backend side of the universal `/openremote-control` command, which
    is invoked from inside the coding agent (Claude Code / Codex / Cursor …) via the
    orc-mcp bridge. It creates a fresh named thread for this connector, rebinds the
    connector to it (so subsequent notify/ask/approve route to this session), and
    announces it to the operator's messaging app(s) of choice.
    """
    account, _ = Account.objects.get_or_create(
        provider=tool or "connector",
        label="connector",
        defaults={"auth_type": "none", "credential_type": "none"},
    )

    session_name = (name or "").strip() or f"{tool or 'session'} {timezone.now():%Y-%m-%d %H:%M}"

    thread = Thread.objects.create(
        name=session_name,
        runtime=tool or "connector",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )

    instance = ConnectorInstance.objects.filter(connector_id=connector_id).first()
    if instance is None:
        ConnectorInstance.objects.create(
            connector_id=connector_id,
            tool=tool,
            workspace_root=workspace_root,
            thread=thread,
        )
    else:
        instance.thread = thread
        instance.workspace_root = workspace_root or instance.workspace_root
        instance.save(update_fields=["thread", "workspace_root", "last_seen_at"])

    _broadcast_text(f"🎮 Remote-control session started: {session_name}")

    return {"thread_id": str(thread.id), "name": session_name}


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

    _broadcast_text(message)


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
    """Best-effort delivery of a prompt to the single active platform. Never raises."""
    from apps.messaging import routing

    recipient = routing.active_recipient()
    if not recipient:
        return
    try:
        if routing.is_telegram():
            from apps.telegram.telegram_api import send_message

            reply_markup = build_reply_markup(prompt)
            text = prompt.question
            if prompt.body:
                text = f"{text}\n\n{prompt.body}"

            async_to_sync(send_message)(
                int(recipient),
                text,
                reply_markup=reply_markup,
            )
        else:
            from apps.gateway.service import enqueue_prompt

            enqueue_prompt(routing.active_platform(), recipient, prompt)
    except Exception:
        logger.exception("connector deliver: delivery failed (best-effort)")
