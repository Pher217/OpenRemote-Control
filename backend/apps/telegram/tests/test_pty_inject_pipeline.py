"""Tests for the Phase 4+5 PTY inject pipeline.

Covers the full path: forum reply → APPROVAL Prompt created (Phase 5 gate) →
callback tap → send_pty_input dispatched (Phase 4 pipeline) → pty.inject frame
delivered to host group.

Security invariants verified here:
  1. Fail-closed: Deny → nothing injected.
  2. Exact-text binding: injected text == approved Prompt's stored text.
  3. Observed sessions never inject (non-PTY reply → no Prompt, read-only reply).
  4. Anti-replay: resolving the same approval twice injects at most once.
  5. Authenticated approver: non-allowlisted callback → no inject.
  (6. Second gate — DANGEROUS input blocked — tested in test_wsclient_pty_inject.py)
  (7. No secrets in payload — tested in test_pty_inject.py)
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import async_to_sync
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
        label=f"pi5-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-pi5-{suffix}",
        credential_recipient=f"r-pi5-{suffix}",
    )


def _make_host(slug):
    from apps.hosts.models import Host

    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_pty_thread(account, host, tmux, topic_id, forum_chat_id):
    from apps.threads.models import Thread

    return Thread.objects.create(
        name=f"pi5-thread-{topic_id}",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=account,
        host=host,
        metadata={
            "tmux_session_name": tmux,
            "telegram_topic_id": topic_id,
            "telegram_forum_chat_id": forum_chat_id,
        },
    )


def _make_observed_thread(account, topic_id, forum_chat_id):
    from apps.threads.models import Thread

    return Thread.objects.create(
        name=f"pi5-obs-thread-{topic_id}",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        account=account,
        metadata={
            "telegram_topic_id": topic_id,
            "telegram_forum_chat_id": forum_chat_id,
        },
    )


async def _make_send():
    calls = []

    async def send(chat_id, text, message_thread_id=None, reply_markup=None):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "thread_id": message_thread_id,
                "reply_markup": reply_markup,
            }
        )

    return send, calls


async def _fake_answer(cq_id, text="", show_alert=False):
    pass


# ---------------------------------------------------------------------------
# Invariant 1 — Fail-closed (Deny → nothing injected)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
async def test_deny_does_not_inject(settings):
    """
    GIVEN a driveable PTY session, a forum reply that creates an APPROVAL Prompt,
          and an operator who taps "deny"
    WHEN  handle_callback_query is called with key="deny"
    THEN  nothing is delivered to the host channel group (fail-closed).
    Invariant 1.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_callback_query, handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("deny1")
    host = await database_sync_to_async(_make_host)("deny-host-1")
    thread = await database_sync_to_async(_make_pty_thread)(
        account, host, "deny-session", topic_id=101, forum_chat_id=-100111
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 101, 111, "echo hello\n", send=send)

    # A prompt should have been created
    assert len(calls) == 1  # approval message sent to operator

    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
        ).first()

    prompt = await _get_prompt()
    assert prompt is not None
    assert prompt.surface_message_ref["action"] == "pty_inject"

    # Register a listener on the host group BEFORE tapping deny
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    # Tap "deny"
    deny_data = f"p:{prompt.nonce}:deny"
    acked = []

    async def capture_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    await handle_callback_query("cq-deny", 111, deny_data, answer=capture_answer)
    assert "Recorded" in acked[0]

    # Nothing should arrive on the host channel
    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.15)
        except (asyncio.TimeoutError, Exception):
            return None

    result = await _try_receive()
    await cl.group_discard(group, ch)

    assert result is None, "Deny must not inject anything into the host"


# ---------------------------------------------------------------------------
# Invariant 2 — Exact-text binding (happy path; also verifies Phase 4 wiring)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
async def test_allow_injects_exact_stored_text(settings):
    """
    GIVEN a driveable PTY session and a forum reply with a specific text
    WHEN  handle_forum_reply creates an APPROVAL Prompt (text bound in
          surface_message_ref["inject_text"]) and the operator taps "allow"
    THEN  a pty.inject frame is delivered to host_{host.id} with text equal to
          the stored inject_text — NOT a re-read of the original message.
    Invariants 1 (allow path) + 2 (exact-text binding).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_callback_query, handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("allow1")
    host = await database_sync_to_async(_make_host)("allow-host-1")
    thread = await database_sync_to_async(_make_pty_thread)(
        account, host, "allow-session", topic_id=102, forum_chat_id=-100111
    )

    inject_text = "ls -la /tmp\n"
    send, calls = await _make_send()
    await handle_forum_reply(-100111, 102, 111, inject_text, send=send)

    assert len(calls) == 1

    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(
            thread=thread, prompt_type=Prompt.PromptType.APPROVAL
        ).first()

    prompt = await _get_prompt()
    assert prompt is not None
    # The stored text must equal what was passed in — exact-text binding
    assert prompt.surface_message_ref["inject_text"] == inject_text

    # Register listener on host group
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    allow_data = f"p:{prompt.nonce}:allow"

    async def capture_answer(cq_id, text="", show_alert=False):
        pass

    await handle_callback_query("cq-allow", 111, allow_data, answer=capture_answer)

    # Wait briefly for the async DB + channel dispatch
    await asyncio.sleep(0.05)

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.3)
        except asyncio.TimeoutError:
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is not None, "Allow must deliver a pty.inject frame to the host"
    assert frame["command"] == "pty.inject"
    assert frame["text"] == inject_text, (
        f"Injected text {frame['text']!r} does not match approved text {inject_text!r}"
    )
    assert frame["approved"] is True
    assert frame["session_name"] == "allow-session"


# ---------------------------------------------------------------------------
# Invariant 3 — Observed sessions never reach send_pty_input
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_observed_thread_reply_does_not_create_approval_prompt(settings):
    """
    GIVEN an OBSERVED (read-only) thread
    WHEN  handle_forum_reply is called
    THEN  no APPROVAL Prompt is created (read-only reply is sent instead).
    Invariant 3: observed sessions never inject.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.telegram.service import handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("obs-pi1")
    thread = await database_sync_to_async(_make_observed_thread)(
        account, topic_id=103, forum_chat_id=-100111
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 103, 111, "some text", send=send)

    # A read-only informational reply is sent, not an approval prompt
    assert len(calls) == 1
    assert "read-only" in calls[0]["text"].lower()

    @database_sync_to_async
    def _count_prompts():
        return Prompt.objects.filter(thread=thread).count()

    count = await _count_prompts()
    assert count == 0, "No APPROVAL Prompt should be created for observed threads"


# ---------------------------------------------------------------------------
# Invariant 4 — Anti-replay: resolve same approval twice → at most one inject
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
async def test_approval_anti_replay_injects_at_most_once(settings):
    """
    GIVEN an APPROVAL Prompt for a pty_inject action
    WHEN  handle_callback_query is called twice with the same nonce + "allow"
    THEN  the second resolve returns None (Prompt no longer PENDING) and exactly
          one frame is delivered to the host.
    Invariant 4: anti-replay.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_callback_query, handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("ar1")
    host = await database_sync_to_async(_make_host)("ar-host-1")
    thread = await database_sync_to_async(_make_pty_thread)(
        account, host, "ar-session", topic_id=104, forum_chat_id=-100111
    )

    send, _ = await _make_send()
    await handle_forum_reply(-100111, 104, 111, "pwd\n", send=send)

    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(thread=thread, prompt_type=Prompt.PromptType.APPROVAL).first()

    prompt = await _get_prompt()
    assert prompt is not None

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    allow_data = f"p:{prompt.nonce}:allow"

    acked = []

    async def capture_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    # First tap — should succeed and inject
    await handle_callback_query("cq-ar-1", 111, allow_data, answer=capture_answer)

    await asyncio.sleep(0.05)

    # Second tap — same nonce, should be rejected by resolve() (already ANSWERED)
    await handle_callback_query("cq-ar-2", 111, allow_data, answer=capture_answer)

    await asyncio.sleep(0.05)

    # Drain all frames from the channel
    frames = []

    async def _drain():
        while True:
            try:
                f = await asyncio.wait_for(cl.receive(ch), timeout=0.15)
                frames.append(f)
            except asyncio.TimeoutError:
                break

    await _drain()
    await cl.group_discard(group, ch)

    # Exactly one pty.inject frame (first tap), zero from second tap
    inject_frames = [f for f in frames if f.get("command") == "pty.inject"]
    assert len(inject_frames) == 1, (
        f"Expected exactly 1 inject frame, got {len(inject_frames)}: {inject_frames}"
    )

    # Second ack should say "Expired or already answered"
    assert len(acked) == 2
    assert "Recorded" in acked[0]
    assert "Expired" in acked[1] or "already" in acked[1]


# ---------------------------------------------------------------------------
# Invariant 5 — Non-allowlisted approver cannot inject
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
async def test_non_allowlisted_approver_cannot_inject(settings):
    """
    GIVEN an APPROVAL Prompt for a pty_inject action
    WHEN  handle_callback_query is called by a user NOT in TELEGRAM_ALLOWED_CHAT_IDS
    THEN  the callback is rejected ("Not authorised") and nothing is injected.
    Invariant 5: authenticated approver.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_callback_query, handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("unauth1")
    host = await database_sync_to_async(_make_host)("unauth-host-1")
    thread = await database_sync_to_async(_make_pty_thread)(
        account, host, "unauth-session", topic_id=105, forum_chat_id=-100111
    )

    send, _ = await _make_send()
    await handle_forum_reply(-100111, 105, 111, "rm -rf /tmp/x\n", send=send)

    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(thread=thread, prompt_type=Prompt.PromptType.APPROVAL).first()

    prompt = await _get_prompt()
    assert prompt is not None

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    allow_data = f"p:{prompt.nonce}:allow"
    acked = []

    async def capture_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    # Tap from non-allowlisted user (999 is not in TELEGRAM_ALLOWED_CHAT_IDS)
    await handle_callback_query("cq-unauth", 999, allow_data, answer=capture_answer)

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.15)
        except asyncio.TimeoutError:
            return None

    result = await _try_receive()
    await cl.group_discard(group, ch)

    assert result is None, "Non-allowlisted approver must not trigger injection"
    assert acked
    assert "authorised" in acked[0].lower() or "authorized" in acked[0].lower()

    # Prompt must still be PENDING (not consumed)
    @database_sync_to_async
    def _check_pending():
        from apps.prompts.models import Prompt as P

        p = P.objects.get(pk=prompt.pk)
        return p.status

    status = await _check_pending()
    assert status == "pending", f"Prompt must remain PENDING after rejected auth, got {status!r}"


# ---------------------------------------------------------------------------
# Approval Prompt text binding — text stored in Prompt == original reply text
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_approval_prompt_stores_exact_reply_text(settings):
    """
    GIVEN a driveable PTY session and a forum reply
    WHEN  handle_forum_reply processes the reply
    THEN  the APPROVAL Prompt's surface_message_ref["inject_text"] equals the
          exact text from the forum reply — proving binding at creation time.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.telegram.service import handle_forum_reply
    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("bind1")
    host = await database_sync_to_async(_make_host)("bind-host-1")
    thread = await database_sync_to_async(_make_pty_thread)(
        account, host, "bind-session", topic_id=106, forum_chat_id=-100111
    )

    reply_text = "cat /etc/hostname\n"
    send, _ = await _make_send()
    await handle_forum_reply(-100111, 106, 111, reply_text, send=send)

    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(thread=thread, prompt_type=Prompt.PromptType.APPROVAL).first()

    prompt = await _get_prompt()
    assert prompt is not None
    assert prompt.surface_message_ref["inject_text"] == reply_text
    assert prompt.surface_message_ref["action"] == "pty_inject"
    assert prompt.surface_message_ref["thread_id"] == str(thread.id)
    # The Prompt's question/body show the text to the operator
    assert "inject" in prompt.question.lower()
    assert repr(reply_text) in prompt.body or reply_text in prompt.body


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
async def test_inject_approval_delivers_tappable_keyboard(settings):
    """
    GIVEN a driveable PTY session and an allowlisted forum reply
    WHEN  handle_forum_reply creates the APPROVAL Prompt and sends it
    THEN  the approval message carries an inline keyboard with tappable
          Allow and Deny buttons whose callback_data binds the Prompt nonce.
    Regression guard: the reply_markup kwarg must reach send().
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.telegram.service import handle_forum_reply

    account = await database_sync_to_async(_make_account)("kbd1")
    host = await database_sync_to_async(_make_host)("kbd-host-1")
    await database_sync_to_async(_make_pty_thread)(
        account, host, "kbd-session", topic_id=120, forum_chat_id=-100111
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 120, 111, "ls\n", send=send)

    assert len(calls) == 1
    markup = calls[0]["reply_markup"]
    assert markup is not None, "approval message must carry an inline keyboard"
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    cbs = [b["callback_data"] for b in buttons]
    assert any(cb.endswith(":allow") for cb in cbs), f"no Allow button: {cbs}"
    assert any(cb.endswith(":deny") for cb in cbs), f"no Deny button: {cbs}"
