"""Service layer for the connectors MCP bridge.

Routes messages, questions, approvals, and session lifecycle events
between coding-agent sessions and the operator's connector chat surfaces.
Best-effort delivery is attempted to the active messaging platform.
"""

import logging
import os
import uuid

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


def _thread_topic(thread) -> tuple[int, int] | None:
    """Return (forum_chat_id, topic_id) when this thread owns a forum topic, else None.

    A connector session gets its own Telegram forum topic at start_session time;
    notify/ask/approve then deliver INTO that topic so the whole session lives in
    one channel the operator can both read and reply in.
    """
    md = thread.metadata or {}
    topic_id = md.get("telegram_topic_id")
    forum_chat_id = md.get("telegram_forum_chat_id")
    if topic_id and forum_chat_id:
        return int(forum_chat_id), int(topic_id)
    return None


def _deliver_text_to_thread(thread, text: str) -> None:
    """Send plain text into the thread's own forum topic, else the active recipient."""
    from apps.messaging import routing

    topic = _thread_topic(thread)
    if topic is not None and routing.is_telegram():
        from apps.telegram.telegram_api import send_message

        forum_chat_id, topic_id = topic
        try:
            async_to_sync(send_message)(forum_chat_id, text, message_thread_id=topic_id)
        except Exception:
            logger.exception("connector deliver: topic text delivery failed (best-effort)")
        return
    _broadcast_text(text)


def _ensure_session_topic(thread, session_name: str) -> None:
    """Create a dedicated Telegram forum topic for this connector session.

    Best-effort: on a non-forum chat or any API error, falls back to a plain
    broadcast so the start announcement is never lost. Stores telegram_topic_id +
    telegram_forum_chat_id on the thread so inbound replies in that topic resolve
    back to this thread (apps.telegram.service._lookup_thread_for_topic).
    """
    from apps.messaging import routing

    if not routing.is_telegram():
        _broadcast_text(f"🎮 Remote-control session started: {session_name}")
        return

    recipient = routing.active_recipient()
    if not recipient:
        return

    try:
        from apps.observe.delivery import pick_color
        from apps.telegram.telegram_api import create_forum_topic, send_message

        forum_chat_id = int(recipient)
        color = pick_color(str(thread.id))
        topic_id = async_to_sync(create_forum_topic)(forum_chat_id, session_name[:128], color)

        thread.metadata = {
            **(thread.metadata or {}),
            "telegram_topic_id": topic_id,
            "telegram_forum_chat_id": forum_chat_id,
        }
        thread.save(update_fields=["metadata"])

        async_to_sync(send_message)(
            forum_chat_id,
            f"🎮 Remote-control session started: {session_name}\n"
            "Reply in this topic to talk to the session.",
            message_thread_id=topic_id,
        )
    except Exception:
        logger.exception("start_session: topic creation failed; falling back to broadcast")
        _broadcast_text(f"🎮 Remote-control session started: {session_name}")


def _compose_session_name(tool: str, workspace_root: str, name: str) -> str:
    """Readable session name.

    An explicit operator-supplied *name* is used verbatim (their choice). When no
    name is given (the bare ``/openremote-control`` dispatch), auto-compose
    ``agent · repo · time``:
    - agent: the coding tool (claude/codex/…); falls back to ``session`` when the
      connector reported none (orc-mcp sends ``unknown`` when ``$ORC_TOOL`` is unset).
    - repo: basename of the workspace root the agent is running in.
    - time: a timestamp, so two un-named sessions in the same repo don't collide.
    """
    title = (name or "").strip()
    if title:
        return title[:255]
    agent = (tool or "").strip()
    if not agent or agent == "unknown":
        agent = "session"
    # Normalize Windows separators so basename works cross-platform on this POSIX
    # backend (else "C:\\Users\\x\\Repo" would leak the whole path into the name).
    norm = (workspace_root or "").strip().replace("\\", "/").rstrip("/")
    repo = os.path.basename(norm)
    parts = [agent]
    if repo:
        parts.append(repo)
    parts.append(f"{timezone.now():%Y-%m-%d %H:%M}")
    return " · ".join(parts)[:255]


def start_session(
    connector_id: str,
    tool: str,
    workspace_root: str,
    name: str,
    claude_session_id: str = "",
    provider: str = "claude",
) -> dict:
    """Start a new remote-control session and dispatch it to the operator's chat.

    This is the backend side of the universal `/openremote-control` command, which
    is invoked from inside the coding agent (Claude Code / Codex / Cursor …) via the
    orc-mcp bridge. It creates a fresh named thread for this connector, rebinds the
    connector to it (so subsequent notify/ask/approve route to this session), and
    announces it to the operator's messaging app(s) of choice.

    When ``claude_session_id`` is provided (the calling Claude Code session's own
    id, read from ``CLAUDE_CODE_SESSION_ID``), the driveable thread is bound to
    THAT existing session — so a Telegram reply runs ``claude -p --resume <id>``
    and continues *this* conversation rather than spinning up a fresh one. When
    omitted, a new session id is minted (a standalone driveable session).
    """
    account, _ = Account.objects.get_or_create(
        provider=tool or "connector",
        label="connector",
        defaults={"auth_type": "none", "credential_type": "none"},
    )

    session_name = _compose_session_name(tool, workspace_root, name)

    # The dispatched chat must be DRIVEABLE (write + stream), never read-only:
    # bind it to the host daemon as a headless `claude` session so a typed reply
    # in the topic routes to `claude -p --session-id/--resume` in the workspace,
    # and the reply streams back into the same topic. Single-host local deploy
    # binds to the sole enrolled host. If no host is enrolled we cannot drive, so
    # fall back to a read-only API thread (and the operator is told to enrol a host).
    from apps.hosts.models import Host  # noqa: PLC0415 — avoid app-load cycle

    # Only auto-bind to a host when it is UNAMBIGUOUS (single enrolled host — the
    # common local single-host deploy). With multiple hosts we cannot tell which
    # machine `workspace_root` lives on (the connector sends no host proof), so we
    # must NOT guess — binding the wrong host would run `claude -p` in the wrong
    # filesystem. Multi-host driveability needs a host hint/proof from the daemon
    # (follow-up); until then multi-host falls back to read-only.
    # SECURITY NOTE: a driveable thread lets an operator-gated Telegram reply run
    # `claude -p --permission-mode bypassPermissions` in `cwd`. Replies are gated by
    # the TELEGRAM allowlist (handle_forum_reply), but a multi-tenant deployment
    # should add a per-connector "drive" scope before enabling this. Acceptable for
    # the single-user local deploy this targets.
    hosts = list(Host.objects.all()[:2])
    host = hosts[0] if len(hosts) == 1 else None
    if host is not None:
        # Bind to the caller's own session when its id is known, so the first
        # Telegram reply resumes THIS conversation (claude_session_started=True
        # makes run_headless try --resume first); otherwise mint a fresh session.
        bound_id = claude_session_id or str(uuid.uuid4())
        thread = Thread.objects.create(
            name=session_name,
            runtime=tool or "claude",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            metadata={
                "headless": True,
                "claude_session_id": bound_id,
                "claude_session_started": bool(claude_session_id),
                "cwd": workspace_root or "",
                "host_name": host.name,
                "provider": provider,
            },
        )
        try:
            from apps.hostlink.service import (
                send_host_command,  # noqa: PLC0415 — avoid app-load cycle
            )

            send_host_command(
                host,
                "tail.start",
                thread_id=str(thread.id),
                claude_session_id=bound_id,
                cwd=workspace_root or "",
                provider="claude",
            )
        except Exception:
            logger.exception("start_session: tail.start dispatch failed; daemon resync covers it")
    else:
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

    _ensure_session_topic(thread, session_name)

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

    _deliver_text_to_thread(thread, message)


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
        # ask_human is human-in-the-loop driving from a phone: give the operator
        # comfortable time to reply (1h) rather than the 15m approval default.
        ttl_seconds=3600,
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


def resolve_pending_ask(text: str, by: str = "", thread=None) -> Prompt | None:
    """Answer the most-recent waiting ``ask_human`` question with a typed reply.

    ``ask_human`` (options-less) creates a FREE_TEXT prompt, delivers it to the
    operator's chat, then polls :func:`result` for an answer. Nothing previously
    turned the operator's typed reply back into that answer, so every ask_human
    timed out. This closes that gap: the operator's next free-text message in the
    prompt chat becomes the answer (request -> answer driving).

    FREE_TEXT prompts are only ever created by :func:`ask`, so selecting on
    prompt_type is sufficient to scope this to connector questions (the chat-LLM
    threads never create them). Returns the resolved Prompt, or None when no
    free-text prompt is awaiting an answer — the caller then falls back to normal
    chat dispatch. The resolve itself is row-locked and anti-replay safe, so a
    concurrent answer/expiry simply yields None here.

    Pass ``thread`` to scope to one session's topic (the forum-reply path, where
    the reply lands in a specific session's topic). Omit it for the threadless
    chat path (General/DM), where the most-recent pending question is answered.
    """
    from apps.prompts.service import resolve

    now = timezone.now()
    qs = Prompt.objects.filter(
        prompt_type=Prompt.PromptType.FREE_TEXT,
        status=Prompt.StatusChoices.PENDING,
        expires_at__gt=now,
    )
    if thread is not None:
        qs = qs.filter(thread=thread)
    pending = qs.order_by("-requested_at").first()
    if pending is None:
        return None
    return resolve(pending.nonce, text=text, by=by)


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

            # Deliver into the session's own topic when it has one, so the
            # question appears in the same channel the operator reads and replies
            # in; otherwise fall back to the active recipient (forum General/DM).
            topic = _thread_topic(prompt.thread)
            chat_id, thread_id = topic if topic is not None else (int(recipient), None)
            async_to_sync(send_message)(
                chat_id,
                text,
                message_thread_id=thread_id,
                reply_markup=reply_markup,
            )
        else:
            from apps.gateway.service import enqueue_prompt

            enqueue_prompt(routing.active_platform(), recipient, prompt)
    except Exception:
        logger.exception("connector deliver: delivery failed (best-effort)")
