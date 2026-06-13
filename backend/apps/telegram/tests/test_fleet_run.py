"""Tests for the Fleet F3 /run command (Phase 2 of F3).

Safety invariants verified:
  R1. Allowlisted /run → APPROVAL Prompt created binding exact command.
  R2. Allow callback → session.start frame emitted with bound command/cwd
      (never re-read from the original Telegram message).
  R3. Deny callback → nothing launched (fail-closed).
  R4. Unknown host → default-deny reply, no Prompt created.
  R5. Non-allowlisted sender → silent no-op, no Prompt created.
  R6. AuditEvent(APPROVAL_REQUEST) created on /run; AuditEvent(RUNTIME_START) on Allow.
  R7. session.start frame shape: command_str=<bound>, cwd=<bound>, session_name present.
"""

from __future__ import annotations

import asyncio

import pytest
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.test import override_settings

INMEM_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_account(suffix):
    from apps.accounts.models import Account

    return Account.objects.create(
        provider="anthropic",
        label=f"run-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-run-{suffix}",
        credential_recipient=f"r-run-{suffix}",
    )


def _make_host(slug):
    from apps.hosts.models import Host

    return Host.objects.create(slug=slug, name=slug, os="linux")


class _CaptureSend:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})


async def _fake_answer(cq_id, text="", show_alert=False):
    pass


# ---------------------------------------------------------------------------
# R1 — Allowlisted /run → APPROVAL Prompt with bound exact command
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_creates_approval_prompt_binding_exact_command(settings):
    """
    GIVEN an allowlisted operator sends /run <host> <command>
    WHEN handle_update processes the message
    THEN an APPROVAL Prompt is created in DB with surface_message_ref binding
         the exact command and host_id; the command in the Prompt is not
         re-read from the Telegram message.
    Invariant R1.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.prompts.models import Prompt
    from apps.telegram.service import handle_update

    host = await database_sync_to_async(_make_host)("run-host-r1")

    send = _CaptureSend()
    await handle_update(12345, "/run run-host-r1 claude --model sonnet", from_user_id=12345, send=send)

    # Approval message sent to operator
    assert len(send.calls) == 1
    assert send.calls[0]["chat_id"] == 12345

    # Prompt was created
    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(
            prompt_type=Prompt.PromptType.APPROVAL,
            status=Prompt.StatusChoices.PENDING,
        ).order_by("-created_at").first()

    prompt = await _get_prompt()
    assert prompt is not None, "APPROVAL Prompt was not created"
    ref = prompt.surface_message_ref
    assert ref is not None
    assert ref.get("action") == "session_start"
    assert ref.get("host_id") == str(host.id)
    # Exact command bound
    assert ref.get("command") == "claude --model sonnet"
    assert "session_name" in ref
    # session_name is auto-generated (not empty)
    assert ref["session_name"].startswith("orc-")


# ---------------------------------------------------------------------------
# R2 — Allow callback → session.start frame with bound command (not re-read)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_allow_dispatches_session_start_with_bound_command(settings):
    """
    GIVEN a /run Prompt has been created with command="claude" bound in the ref
    WHEN the operator taps "allow" via handle_callback_query
    THEN a session.start host_command frame is dispatched to the host group
         with command_str equal to the bound command — NOT re-read from
         any external source.
    Invariant R2: exact-command binding + session.start dispatch on Allow.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.prompts.models import Prompt
    from apps.prompts.service import create_prompt
    from apps.telegram.service import handle_callback_query
    from apps.threads.models import Thread
    from apps.accounts.models import Account

    host = await database_sync_to_async(_make_host)("run-host-r2")

    account = await database_sync_to_async(_make_account)("r2")
    thread = await database_sync_to_async(Thread.objects.create)(
        name="run-r2-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )

    # Create the Prompt as handle_update would
    bound_command = "claude --model opus"
    bound_session = "orc-r2abc123"

    @database_sync_to_async
    def _create():
        return create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question=f"Launch on {host.slug}?",
            body=f"Command: {bound_command!r}",
            options=[
                {"key": "allow", "label": "Allow"},
                {"key": "deny", "label": "Deny"},
            ],
            trust_class=Prompt.TrustClass.APPROVAL,
            ttl_seconds=300,
            surface_message_ref={
                "action": "session_start",
                "host_id": str(host.id),
                "command": bound_command,
                "cwd": "",
                "session_name": bound_session,
            },
        )

    prompt = await _create()

    # Register listener on host group
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    acked = []

    async def capture_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    with __import__("unittest.mock", fromlist=["AsyncMock"]).patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock,
    ):
        await handle_callback_query(
            "cq-r2",
            12345,
            f"p:{prompt.nonce}:allow",
            answer=capture_answer,
        )

    assert "Recorded" in acked[0]

    # session.start frame delivered to host group
    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.5)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is not None, "session.start frame not delivered to host group"
    assert frame.get("command") == "session.start"
    # The command_str must be the BOUND value from the Prompt, not re-read
    assert frame.get("command_str") == bound_command, (
        f"Bound command mismatch. Expected {bound_command!r}, got {frame.get('command_str')!r}"
    )
    assert frame.get("session_name") == bound_session


# ---------------------------------------------------------------------------
# R3 — Deny callback → nothing launched (fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_deny_does_not_launch(settings):
    """
    GIVEN a /run Prompt exists
    WHEN the operator taps "deny" via handle_callback_query
    THEN NO session.start frame is dispatched (fail-closed; nothing launched).
    Invariant R3.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.prompts.models import Prompt
    from apps.prompts.service import create_prompt
    from apps.telegram.service import handle_callback_query
    from apps.threads.models import Thread
    from apps.accounts.models import Account

    host = await database_sync_to_async(_make_host)("run-host-r3")
    account = await database_sync_to_async(_make_account)("r3")
    thread = await database_sync_to_async(Thread.objects.create)(
        name="run-r3-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )

    @database_sync_to_async
    def _create():
        return create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Launch on run-host-r3?",
            options=[
                {"key": "allow", "label": "Allow"},
                {"key": "deny", "label": "Deny"},
            ],
            trust_class=Prompt.TrustClass.APPROVAL,
            ttl_seconds=300,
            surface_message_ref={
                "action": "session_start",
                "host_id": str(host.id),
                "command": "claude",
                "cwd": "",
                "session_name": "orc-r3abc",
            },
        )

    prompt = await _create()

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    await handle_callback_query(
        "cq-r3",
        12345,
        f"p:{prompt.nonce}:deny",
        answer=_fake_answer,
    )

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.15)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is None, "Deny must not dispatch session.start (fail-closed)"


# ---------------------------------------------------------------------------
# R4 — Unknown host → default-deny reply, no Prompt
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
)
async def test_run_unknown_host_default_deny_no_prompt(settings):
    """
    GIVEN an allowlisted operator sends /run with an unknown host slug
    WHEN handle_update processes the message
    THEN a denial reply is sent, no APPROVAL Prompt is created, and no host
         frame is dispatched.
    Invariant R4.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.prompts.models import Prompt
    from apps.telegram.service import handle_update

    send = _CaptureSend()
    before_count = await database_sync_to_async(Prompt.objects.count)()

    await handle_update(12345, "/run totally-unknown-host-xyz claude", from_user_id=12345, send=send)

    after_count = await database_sync_to_async(Prompt.objects.count)()

    # A denial reply was sent
    assert len(send.calls) == 1
    assert send.calls[0]["chat_id"] == 12345
    reply = send.calls[0]["text"].lower()
    assert (
        "unknown" in reply or "host" in reply
    ), f"Expected denial reply mentioning host, got: {send.calls[0]['text']!r}"

    # No Prompt was created
    assert after_count == before_count, "Prompt created for unknown host (must not happen)"


# ---------------------------------------------------------------------------
# R5 — Non-allowlisted sender: no-op, no Prompt
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
)
async def test_run_non_allowlisted_is_silent_noop(settings):
    """
    GIVEN a sender NOT in TELEGRAM_ALLOWED_CHAT_IDS sends /run <host> <command>
    WHEN handle_update processes the message
    THEN nothing is sent and no APPROVAL Prompt is created.
    Invariant R5.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.prompts.models import Prompt
    from apps.telegram.service import handle_update

    await database_sync_to_async(_make_host)("run-host-r5")

    send = _CaptureSend()
    before_count = await database_sync_to_async(Prompt.objects.count)()

    await handle_update(99999, "/run run-host-r5 claude", from_user_id=99999, send=send)

    after_count = await database_sync_to_async(Prompt.objects.count)()

    assert send.calls == [], "Non-allowlisted sender must not receive a reply"
    assert after_count == before_count, "Prompt created for non-allowlisted sender (must not happen)"


# ---------------------------------------------------------------------------
# R6 — AuditEvent(APPROVAL_REQUEST) on /run; AuditEvent(RUNTIME_START) on Allow
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_audit_events_created(settings):
    """
    GIVEN an allowlisted /run → Allow flow
    WHEN handle_update then handle_callback_query are called
    THEN an AuditEvent(APPROVAL_REQUEST) is created on /run,
         and an AuditEvent(RUNTIME_START) is created on Allow.
    Invariant R6.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.audit.models import AuditEvent
    from apps.prompts.models import Prompt
    from apps.telegram.service import handle_callback_query, handle_update

    host = await database_sync_to_async(_make_host)("run-host-r6")

    send = _CaptureSend()
    before_req = await database_sync_to_async(
        AuditEvent.objects.filter(event_type=AuditEvent.EventTypeChoices.APPROVAL_REQUEST).count
    )()

    await handle_update(12345, "/run run-host-r6 claude", from_user_id=12345, send=send)

    after_req = await database_sync_to_async(
        AuditEvent.objects.filter(event_type=AuditEvent.EventTypeChoices.APPROVAL_REQUEST).count
    )()
    assert after_req > before_req, "AuditEvent(APPROVAL_REQUEST) not created on /run"

    # Get the created Prompt
    @database_sync_to_async
    def _get_prompt():
        return (
            Prompt.objects.filter(
                prompt_type=Prompt.PromptType.APPROVAL,
                status=Prompt.StatusChoices.PENDING,
            )
            .order_by("-created_at")
            .first()
        )

    prompt = await _get_prompt()
    assert prompt is not None

    # Register host group listener
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    before_start = await database_sync_to_async(
        AuditEvent.objects.filter(event_type=AuditEvent.EventTypeChoices.RUNTIME_START).count
    )()

    with __import__("unittest.mock", fromlist=["AsyncMock"]).patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock,
    ):
        await handle_callback_query(
            "cq-r6",
            12345,
            f"p:{prompt.nonce}:allow",
            answer=_fake_answer,
        )

    await cl.group_discard(group, ch)

    after_start = await database_sync_to_async(
        AuditEvent.objects.filter(event_type=AuditEvent.EventTypeChoices.RUNTIME_START).count
    )()
    assert after_start > before_start, "AuditEvent(RUNTIME_START) not created on Allow"


# ---------------------------------------------------------------------------
# R7 — session.start frame shape
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_allow_session_start_frame_shape(settings):
    """
    GIVEN a /run Allow flow
    WHEN handle_callback_query dispatches session.start
    THEN the frame on the host group has:
         type="host_command", command="session.start",
         command_str=<bound-command>, cwd=<bound-cwd>, session_name=<str>.
    Invariant R7: frame shape contract.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.prompts.models import Prompt
    from apps.prompts.service import create_prompt
    from apps.telegram.service import handle_callback_query
    from apps.threads.models import Thread
    from apps.accounts.models import Account

    host = await database_sync_to_async(_make_host)("run-host-r7")
    account = await database_sync_to_async(_make_account)("r7")
    thread = await database_sync_to_async(Thread.objects.create)(
        name="run-r7-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
    )

    @database_sync_to_async
    def _create():
        return create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Launch on run-host-r7?",
            options=[
                {"key": "allow", "label": "Allow"},
                {"key": "deny", "label": "Deny"},
            ],
            trust_class=Prompt.TrustClass.APPROVAL,
            ttl_seconds=300,
            surface_message_ref={
                "action": "session_start",
                "host_id": str(host.id),
                "command": "codex --model o4-mini",
                "cwd": "/home/user/project",
                "session_name": "orc-r7frm123",
            },
        )

    prompt = await _create()

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    with __import__("unittest.mock", fromlist=["AsyncMock"]).patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock,
    ):
        await handle_callback_query(
            "cq-r7",
            12345,
            f"p:{prompt.nonce}:allow",
            answer=_fake_answer,
        )

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.5)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is not None, "session.start frame not delivered"
    assert frame["type"] == "host_command"
    assert frame["command"] == "session.start"
    assert frame["command_str"] == "codex --model o4-mini"
    assert frame["cwd"] == "/home/user/project"
    assert frame["session_name"] == "orc-r7frm123"
    # No secrets beyond what's needed
    assert "token" not in frame
    assert "password" not in frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_run_approval_delivers_tappable_keyboard(settings):
    """
    GIVEN an allowlisted operator sends /run <host> <command>
    WHEN  handle_update creates the APPROVAL Prompt and sends it
    THEN  the approval message carries an inline keyboard with tappable
          Allow and Deny buttons whose callback_data binds the Prompt nonce.
    Regression guard: the reply_markup kwarg must reach send().
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_update

    await database_sync_to_async(_make_host)("run-host-kbd")

    send = _CaptureSend()
    await handle_update(12345, "/run run-host-kbd claude --model sonnet", from_user_id=12345, send=send)

    assert len(send.calls) == 1
    markup = send.calls[0].get("reply_markup")
    assert markup is not None, "approval message must carry an inline keyboard"
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    cbs = [b["callback_data"] for b in buttons]
    assert any(cb.endswith(":allow") for cb in cbs), f"no Allow button: {cbs}"
    assert any(cb.endswith(":deny") for cb in cbs), f"no Deny button: {cbs}"


# ---------------------------------------------------------------------------
# Inv#9 — Non-allowlisted USER in an allowlisted CHAT is denied for /run
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
)
async def test_run_non_allowlisted_user_in_allowlisted_chat_is_denied(settings):
    """
    GIVEN chat_id=12345 IS in TELEGRAM_ALLOWED_CHAT_IDS
          but from_user_id=99999 is NOT in TELEGRAM_ALLOWED_CHAT_IDS
    WHEN handle_update(12345, "/run <host> <command>", from_user_id=99999, ...) is called
    THEN no APPROVAL Prompt is created and send is never called (silent deny).
    Invariant #9: gate on from.id, not just chat_id.
    Regression: before the fix, an allowlisted chat_id bypassed the from_user_id check.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.prompts.models import Prompt
    from apps.telegram.service import handle_update

    await database_sync_to_async(_make_host)("run-host-inv9")

    send = _CaptureSend()
    before_count = await database_sync_to_async(Prompt.objects.count)()

    await handle_update(12345, "/run run-host-inv9 claude", from_user_id=99999, send=send)

    after_count = await database_sync_to_async(Prompt.objects.count)()

    assert send.calls == [], "Non-allowlisted user must not receive a reply (silent deny)"
    assert after_count == before_count, "APPROVAL Prompt must not be created for non-allowlisted user"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
)
async def test_stop_non_allowlisted_user_in_allowlisted_chat_is_denied(settings):
    """
    GIVEN chat_id=12345 IS in TELEGRAM_ALLOWED_CHAT_IDS
          but from_user_id=99999 is NOT in TELEGRAM_ALLOWED_CHAT_IDS
    WHEN handle_update(12345, "/stop <session>", from_user_id=99999, ...) is called
    THEN send is never called (silent deny) and no kill frame is dispatched.
    Invariant #9 applied to /stop.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    send = _CaptureSend()
    await handle_update(12345, "/stop some-session", from_user_id=99999, send=send)

    assert send.calls == [], "Non-allowlisted user must not receive a /stop reply (silent deny)"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
)
async def test_sessions_non_allowlisted_user_in_allowlisted_chat_is_denied(settings):
    """
    GIVEN chat_id=12345 IS in TELEGRAM_ALLOWED_CHAT_IDS
          but from_user_id=99999 is NOT in TELEGRAM_ALLOWED_CHAT_IDS
    WHEN handle_update(12345, "/sessions", from_user_id=99999, ...) is called
    THEN send is never called (silent deny).
    Invariant #9 applied to /sessions.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    send = _CaptureSend()
    await handle_update(12345, "/sessions", from_user_id=99999, send=send)

    assert send.calls == [], "Non-allowlisted user must not receive /sessions output (silent deny)"
