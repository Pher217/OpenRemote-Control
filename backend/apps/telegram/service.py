"""Core Telegram surface service.

Maps Telegram chats/forum topics to Threads and handles inbound activity:
plain messages, forum-topic replies, inline-button callback queries (prompt
and approval answers), and the operator slash commands ``/sessions``,
``/stop``, ``/run``, and ``/pair`` — including operator auth gating, host
resolution, session-start prompts, pairing creation, and audit logging.
"""

import datetime as _dt
import logging
import uuid
from datetime import timedelta

from channels.db import database_sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Account
from apps.audit.models import AuditEvent
from apps.hosts.models import Host
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt
from apps.prompts.service import resolve as resolve_prompt
from apps.prompts.surfaces.telegram import build_reply_markup, parse_callback
from apps.slash.fleet_dashboard import refresh_fleet_dashboard
from apps.slash.handlers.sessions import _active_threads, render_fleet
from apps.supervisor.activity import render_fleet_with_activity
from apps.supervisor.fleet_state import build_fleet_state
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


@database_sync_to_async
def _resolve_host(host_arg: str):
    """Resolve a host by slug or name (case-insensitive). Returns Host or None."""
    return (
        Host.objects.filter(slug__iexact=host_arg).first()
        or Host.objects.filter(name__iexact=host_arg).first()
    )


@database_sync_to_async
def _list_host_slugs():
    """Return a sorted list of all host slugs (for error messages)."""
    return sorted(Host.objects.values_list("slug", flat=True))


@database_sync_to_async
def _resolve_pty_thread_for_stop(session_arg: str):
    """Resolve a running PTY Thread by tmux_session_name or thread UUID prefix.

    Returns (thread, error_message).  On success thread is a Thread instance
    and error_message is None.  On failure thread is None and error_message
    describes the problem.
    """
    # Try exact tmux_session_name match first.
    qs = Thread.objects.select_related("host").filter(
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        status=Thread.StatusChoices.RUNNING,
        metadata__tmux_session_name=session_arg,
    )
    results = list(qs[:2])
    if len(results) == 1:
        return results[0], None
    if len(results) > 1:
        return None, f"Ambiguous: multiple running sessions named {session_arg!r}. Use the thread id."

    # Try UUID prefix match (short thread id).
    try:
        # Allow full UUID or a prefix of at least 8 hex chars.
        prefix = session_arg.replace("-", "").lower()
        if len(prefix) < 8:
            return None, f"Session identifier {session_arg!r} is too short — use tmux session name or full thread id."
        # Fetch all running PTY threads and filter by UUID hex prefix.
        candidates = [
            t for t in Thread.objects.select_related("host").filter(
                runtime_mode=Thread.RuntimeModeChoices.PTY,
                status=Thread.StatusChoices.RUNNING,
            )
            if str(t.id).replace("-", "").startswith(prefix)
        ]
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            return None, "Ambiguous: multiple sessions match that id prefix. Use the full thread id."
    except Exception:
        pass

    return None, f"No running PTY session found for {session_arg!r}."


@database_sync_to_async
def _stop_thread(thread_id, actor: str):
    """Mark a thread as STOPPED and write an audit event. Returns True on success."""
    with transaction.atomic():
        updated = Thread.objects.filter(
            id=thread_id,
            status=Thread.StatusChoices.RUNNING,
        ).update(status=Thread.StatusChoices.STOPPED, ended_at=timezone.now())
        if updated:
            AuditEvent.objects.create(
                thread_id=thread_id,
                actor=actor,
                event_type=AuditEvent.EventTypeChoices.RUNTIME_STOP,
                redacted_payload={"source": "telegram_stop"},
            )
    return updated > 0


@database_sync_to_async
def _create_session_start_prompt(thread, host, command: str, cwd: str, session_name: str):
    """Create an APPROVAL Prompt binding the session.start parameters."""
    return create_prompt(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question=f"Launch on {host.slug}?",
        body=f"Command: {command!r}\nCwd: {cwd or '(default)'}",
        options=[
            {"key": "allow", "label": "Allow"},
            {"key": "deny", "label": "Deny"},
        ],
        trust_class=Prompt.TrustClass.APPROVAL,
        ttl_seconds=300,
        surface_message_ref={
            "action": "session_start",
            "host_id": str(host.id),
            "command": command,
            "cwd": cwd,
            "session_name": session_name,
        },
    )


@database_sync_to_async
def _audit_session_start_request(actor: str, host_id: str, command: str):
    """Write an APPROVAL_REQUEST audit event for a /run command."""
    AuditEvent.objects.create(
        thread=None,
        actor=actor,
        event_type=AuditEvent.EventTypeChoices.APPROVAL_REQUEST,
        redacted_payload={
            "source": "telegram_run",
            "host_id": host_id,
            "command": command,
        },
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
    Behaviour:
      - Unknown topic → inform user.
      - Pending ask_human prompt for this thread → resolve it with the reply.
      - Non-driveable thread (no host) → "doesn't accept typed input" message.
      - Headless session → dispatch headless.prompt to the host.
      - Driveable PTY session → inject keystrokes (auto-approve or approval prompt).
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

    # --- Pending ask_human answer --------------------------------------------
    # A connector session delivers its ask_human question into its own topic; a
    # typed reply here is that question's answer. Resolve a pending free-text
    # prompt for THIS thread before the read-only/inject logic (a connector
    # thread is API-mode and would otherwise bounce as read-only).
    from apps.connectors.service import resolve_pending_ask  # noqa: PLC0415

    resolved = await database_sync_to_async(resolve_pending_ask)(
        text, by=str(from_user_id), thread=thread
    )
    if resolved is not None:
        await send(
            forum_chat_id,
            "✓ Got it — sent to the session.",
            message_thread_id=message_thread_id,
        )
        return

    # --- Non-driveable guard -------------------------------------------------
    # Every chat surface is driveable by design — only headless Claude and PTY
    # sessions land here. A thread with no host (e.g. a connector API session
    # whose ask was already resolved above) cannot take typed input.
    is_pty = thread.runtime_mode == Thread.RuntimeModeChoices.PTY
    has_host = thread.host_id is not None
    is_headless = bool((thread.metadata or {}).get("headless"))
    has_tmux = bool((thread.metadata or {}).get("tmux_session_name"))

    if not (has_host and (is_headless or (is_pty and has_tmux))):
        await send(
            forum_chat_id,
            "This session doesn't accept typed input. Start a driveable session "
            "with `/openremote-control` (or `orc run`) to chat with it.",
            message_thread_id=message_thread_id,
        )
        return

    # --- Headless Claude session dispatch ------------------------------------
    if is_headless:
        md = thread.metadata or {}

        @database_sync_to_async
        def _fetch_host():
            return Thread.objects.select_related("host").get(id=thread.id)

        t_h = await _fetch_host()
        from apps.hostlink.service import send_host_command  # noqa: PLC0415

        await database_sync_to_async(send_host_command)(
            t_h.host,
            "headless.prompt",
            claude_session_id=md.get("claude_session_id"),
            cwd=md.get("cwd", ""),
            text=text,
            thread_id=str(thread.id),
            started=bool(md.get("claude_session_started")),
        )
        return

    # --- Driveable PTY session -----------------------------------------------
    # Auto-approve mode: when this session is marked trusted (metadata
    # auto_approve=True), inject directly without a per-message Allow tap.
    # DANGEROUS input is still blocked at the host send_keys layer
    # (input_policy.classify_input), so this skips the human tap, NOT the
    # dangerous-input guard. Identity is already gated above (from.id allowlist).
    if (thread.metadata or {}).get("auto_approve") is True:

        @database_sync_to_async
        def _fetch_with_host():
            return Thread.objects.select_related("host").get(id=thread.id)

        t_h = await _fetch_with_host()
        from apps.hostlink.service import async_send_pty_input  # noqa: PLC0415

        try:
            await async_send_pty_input(t_h, text, approved=True)
        except Exception:
            log.exception("auto_approve inject failed for thread %s", thread.id)
        return

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

    # Deliver the approval request inline via the injected send callable. The
    # production send is telegram_api.send_message, which accepts reply_markup,
    # so the Allow/Deny inline keyboard is delivered here. Fail-closed: if
    # delivery fails, nothing is injected (the Prompt stays PENDING until expiry).
    reply_markup = build_reply_markup(prompt)
    msg = prompt.question
    if prompt.body:
        msg = f"{msg}\n\n{prompt.body}"
    await send(
        forum_chat_id,
        msg,
        message_thread_id=message_thread_id,
        reply_markup=reply_markup,
    )


async def handle_update(chat_id: int, text: str, *, from_user_id: int | None, send):
    if chat_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        return

    stripped = text.strip()
    # Privileged fleet/admin commands require the AUTHENTICATED USER in the allowlist,
    # not merely an allowlisted chat (invariant #9). Fail-closed when from_user_id is
    # missing or not allowlisted.
    if stripped.startswith(("/sessions", "/stop", "/run", "/pair")):  # noqa: SIM102 — keep nested for the auth-gate comment above
        if from_user_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
            return

    # /sessions — global fleet view (operator-only; auth gate is the check above).
    if stripped.startswith("/sessions"):
        threads = await database_sync_to_async(_active_threads)()
        now = _dt.datetime.now(tz=_dt.UTC)
        fleet_text = render_fleet(threads, now)
        await send(chat_id, fleet_text, parse_mode="HTML")
        await refresh_fleet_dashboard()
        return

    # /stop <session> — kill a running PTY session (no approval needed; kill switch).
    if stripped.startswith("/stop"):
        await _handle_stop_command(chat_id, stripped, send=send)
        return

    # /run <host> <command...> — launch a PTY session (approval-gated).
    if stripped.startswith("/run"):
        await _handle_run_command(chat_id, stripped, send=send)
        return

    # /pair [tool] [label] — create a pairing code and send the QR image.
    if stripped.startswith("/pair"):
        parts = stripped.split(maxsplit=3)
        tool = parts[1] if len(parts) > 1 else ""
        label = parts[2] if len(parts) > 2 else ""
        await _handle_pair_command(chat_id, tool, label)
        return

    # An ask_human question delivered to this chat turns the operator's next
    # typed message into its answer (request -> answer driving) instead of
    # routing it to the chat LLM. Operator-gated: only an allowlisted operator
    # (not merely an allowlisted chat) can answer a pending connector prompt.
    if from_user_id in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        from apps.connectors.service import resolve_pending_ask  # noqa: PLC0415

        resolved = await database_sync_to_async(resolve_pending_ask)(
            text, by=str(from_user_id)
        )
        if resolved is not None:
            await send(chat_id, "✓ Answer sent to the waiting session.")
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

    # The fleet digest (which sessions exist, what they're doing) is operator-only
    # information — the same read-boundary as /sessions, which gates on from.id
    # (invariant #9). A non-operator in an allowlisted group/forum chat may still
    # use general chat, but must NOT receive the fleet digest. So inject it only
    # for an authenticated, allowlisted operator. Non-operators get plain chat.
    fleet_context = None
    if from_user_id in settings.TELEGRAM_ALLOWED_CHAT_IDS:

        def _build_fleet_context():
            fleet = build_fleet_state()
            return (
                "Currently active coding sessions on this machine:\n"
                + render_fleet_with_activity(fleet_state=fleet)
            )

        fleet_context = await database_sync_to_async(_build_fleet_context)()
    await dispatch_text(thread, text, on_event=on_event, extra_system_context=fleet_context)

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

    # Dispatch based on which action was approved.
    ref = prompt.surface_message_ref or {}
    action = ref.get("action", "")

    if action == "pty_inject" and key == "allow":
        # Phase 5: PTY keystroke injection.  The inject_text bound at
        # approval-creation time is used — never the raw Telegram message.
        thread_id = ref.get("thread_id")
        inject_text = ref.get("inject_text", "")
        if thread_id and inject_text:
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

    elif action == "session_start" and key == "allow":
        # Fleet F3 /run: launch the PTY session on the host using the bound
        # command and cwd — never re-read from any Telegram message.
        # Fail-closed: any error is logged; the operator's ack has already been sent.
        host_id = ref.get("host_id", "")
        command = ref.get("command", "")
        cwd = ref.get("cwd", "") or ""
        session_name = ref.get("session_name", "")
        if host_id and command and session_name:
            try:
                @database_sync_to_async
                def _fetch_host():
                    from apps.hosts.models import Host as _Host  # noqa: PLC0415

                    try:
                        return _Host.objects.get(id=host_id)
                    except _Host.DoesNotExist:
                        log.error("session_start: host %s not found after approval", host_id)
                        return None

                host = await _fetch_host()
                if host is not None:
                    from apps.hostlink.service import send_host_command  # noqa: PLC0415

                    await database_sync_to_async(send_host_command)(
                        host,
                        "session.start",
                        session_name=session_name,
                        command_str=command,
                        cwd=cwd,
                    )
                    log.info(
                        "session_start: dispatched session.start to host %s session %r",
                        host_id,
                        session_name,
                    )
                    # Audit the launch
                    await database_sync_to_async(AuditEvent.objects.create)(
                        thread=None,
                        actor=str(from_user_id),
                        event_type=AuditEvent.EventTypeChoices.RUNTIME_START,
                        redacted_payload={
                            "source": "telegram_run_allow",
                            "host_id": host_id,
                            "session_name": session_name,
                        },
                    )
                    # Refresh fleet dashboard after launch.
                    await refresh_fleet_dashboard()
            except Exception:
                log.exception("session_start: dispatch failed after approval")


async def _handle_stop_command(chat_id: int, text: str, *, send) -> None:
    """Handle /stop <session> — identity-gated, no approval (kill-switch).

    Resolves to a running PTY Thread by tmux_session_name or thread UUID prefix.
    Observed / non-PTY sessions → explicit "read-only" reply (never silent drop).
    """
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await send(chat_id, "Usage: /stop <session-name-or-id>")
        return

    session_arg = parts[1].strip()
    thread, error_msg = await _resolve_pty_thread_for_stop(session_arg)

    if thread is None:
        await send(chat_id, error_msg or f"No running PTY session found for {session_arg!r}.")
        return

    # Emit session.kill to the host daemon.
    host = thread.host
    session_name = (thread.metadata or {}).get("tmux_session_name", session_arg)

    if host is None:
        await send(chat_id, f"Session {session_arg!r} has no linked host — cannot stop remotely.")
        return

    from apps.hostlink.service import send_host_command  # noqa: PLC0415

    await database_sync_to_async(send_host_command)(
        host,
        "session.kill",
        session_name=session_name,
    )

    # Mark Thread as STOPPED + audit.
    stopped = await _stop_thread(thread.id, actor=str(chat_id))
    if stopped:
        await send(chat_id, f"Stopped session {session_name!r} on {host.slug}.")
    else:
        # Race: already stopped between our resolve and the update.
        await send(chat_id, f"Session {session_name!r} was already stopped.")

    # Refresh fleet dashboard.
    await refresh_fleet_dashboard()


async def _handle_run_command(chat_id: int, text: str, *, send) -> None:
    """Handle /run <host> <command...> — approval-gated PTY launch.

    Creates an APPROVAL Prompt binding {host_id, command, cwd} (no re-read of
    the raw Telegram message after this point).  On Allow in handle_callback_query
    → session.start is dispatched to the host daemon.
    """
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].strip() or not parts[2].strip():
        await send(chat_id, "Usage: /run <host> <command...>")
        return

    host_arg = parts[1].strip()
    command = parts[2].strip()
    cwd = ""  # Default cwd (tmux server default); could be extended later.

    # Resolve host — default-deny on unknown.
    host = await _resolve_host(host_arg)
    if host is None:
        slugs = await _list_host_slugs()
        slugs_str = ", ".join(slugs) if slugs else "(none registered)"
        await send(
            chat_id,
            f"Unknown host {host_arg!r}. Known hosts: {slugs_str}",
        )
        return

    # Generate a session name for this launch so it can be bound in the Prompt.
    session_name = f"orc-{uuid.uuid4().hex[:8]}"

    # We need a Thread to bind the Prompt to.  Use/create the operator's chat thread.
    thread = await database_sync_to_async(get_or_create_thread_for_chat)(chat_id)

    # Create an APPROVAL Prompt binding the exact launch parameters.
    prompt = await _create_session_start_prompt(thread, host, command, cwd, session_name)

    # Audit the request.
    await _audit_session_start_request(
        actor=str(chat_id),
        host_id=str(host.id),
        command=command,
    )

    # Send the approval request to the operator (with the Allow/Deny keyboard).
    reply_markup = build_reply_markup(prompt)
    msg = prompt.question
    if prompt.body:
        msg = f"{msg}\n\n{prompt.body}"
    await send(chat_id, msg, reply_markup=reply_markup)


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
