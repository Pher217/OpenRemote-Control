"""DB integration tests for apps/matrix/service.py."""

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.matrix.models import MatrixRoom
from apps.matrix.service import get_or_create_thread_for_room, handle_message
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt
from apps.threads.models import Thread

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_account(suffix="mx1"):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"x",
        credential_key_id=f"k-{suffix}",
        credential_recipient=f"r-{suffix}",
    )


def make_thread(account, name="mx-test"):
    return Thread.objects.create(
        name=name,
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=account,
    )


async def _async_send():
    """Return a capturing async send callable."""
    sent = []

    async def _send(room_id, text):
        sent.append((room_id, text))

    return _send, sent


# ---------------------------------------------------------------------------
# get_or_create_thread_for_room
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetOrCreateThreadForRoom:
    def test_creates_thread_and_room(self):
        """GIVEN a new room_id WHEN get_or_create_thread_for_room THEN Thread + MatrixRoom exist."""
        room_id = "!new:example.org"
        thread = get_or_create_thread_for_room(room_id)

        assert thread is not None
        assert thread.runtime == "matrix"
        assert thread.runtime_mode == Thread.RuntimeModeChoices.API

        room = MatrixRoom.objects.get(room_id=room_id)
        assert room.thread_id == thread.id

    def test_idempotent_returns_same_thread(self):
        """GIVEN existing room WHEN called twice THEN same Thread returned, no duplicates."""
        room_id = "!existing:example.org"
        t1 = get_or_create_thread_for_room(room_id)
        t2 = get_or_create_thread_for_room(room_id)

        assert t1.id == t2.id
        assert MatrixRoom.objects.filter(room_id=room_id).count() == 1

    def test_creates_matrix_account(self):
        """GIVEN no prior matrix Account WHEN room created THEN Account(provider=matrix) exists."""
        room_id = "!acct:example.org"
        get_or_create_thread_for_room(room_id)

        account = Account.objects.filter(provider="matrix", label="matrix").first()
        assert account is not None

    def test_reuses_existing_account(self):
        """GIVEN existing matrix Account WHEN two rooms created THEN only one Account."""
        get_or_create_thread_for_room("!r1:example.org")
        get_or_create_thread_for_room("!r2:example.org")

        assert Account.objects.filter(provider="matrix", label="matrix").count() == 1


# ---------------------------------------------------------------------------
# handle_message — resolves a pending CHOICE_SINGLE prompt
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_message_resolves_choice_prompt():
    """GIVEN a PENDING CHOICE_SINGLE prompt WHEN user replies '1' THEN prompt answered."""
    room_id = "!choice:example.org"

    account = await database_sync_to_async(make_account)("ch1")
    thread = await database_sync_to_async(make_thread)(account, "mx-choice-1")
    await database_sync_to_async(
        lambda: MatrixRoom.objects.create(room_id=room_id, thread=thread)
    )()

    options = [{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}]
    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.CHOICE_SINGLE,
        question="Proceed?",
        options=options,
    )

    send, sent = await _async_send()
    await handle_message(room_id, "@user:example.org", "1", send=send)

    assert len(sent) == 1
    room_out, text_out = sent[0]
    assert room_out == room_id
    assert "Recorded" in text_out

    @database_sync_to_async
    def _status():
        return Prompt.objects.get(pk=prompt.pk).status

    assert await _status() == Prompt.StatusChoices.ANSWERED


# ---------------------------------------------------------------------------
# handle_message — rejects approval from non-approved MXID
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_message_rejects_unapproved_mxid(settings):
    """GIVEN APPROVAL prompt WHEN sender not in MATRIX_APPROVED_MXIDS THEN 'Not authorised'."""
    settings.MATRIX_APPROVED_MXIDS = ["@admin:example.org"]

    room_id = "!approval:example.org"

    account = await database_sync_to_async(make_account)("ap1")
    thread = await database_sync_to_async(make_thread)(account, "mx-approval-1")
    await database_sync_to_async(
        lambda: MatrixRoom.objects.create(room_id=room_id, thread=thread)
    )()

    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question="Deploy to prod?",
        trust_class=Prompt.TrustClass.APPROVAL,
    )

    send, sent = await _async_send()
    await handle_message(room_id, "@intruder:example.org", "1", send=send)

    assert len(sent) == 1
    assert "Not authorised" in sent[0][1]

    @database_sync_to_async
    def _status():
        return Prompt.objects.get(pk=prompt.pk).status

    assert await _status() == Prompt.StatusChoices.PENDING


# ---------------------------------------------------------------------------
# handle_message — approved MXID resolves the approval prompt
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_message_approved_mxid_resolves_approval(settings):
    """GIVEN APPROVAL prompt WHEN sender IS in MATRIX_APPROVED_MXIDS THEN resolved."""
    settings.MATRIX_APPROVED_MXIDS = ["@admin:example.org"]

    room_id = "!approval2:example.org"

    account = await database_sync_to_async(make_account)("ap2")
    thread = await database_sync_to_async(make_thread)(account, "mx-approval-2")
    await database_sync_to_async(
        lambda: MatrixRoom.objects.create(room_id=room_id, thread=thread)
    )()

    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question="Deploy?",
        trust_class=Prompt.TrustClass.APPROVAL,
    )

    send, sent = await _async_send()
    await handle_message(room_id, "@admin:example.org", "allow", send=send)

    assert len(sent) == 1
    assert "Recorded" in sent[0][1]

    @database_sync_to_async
    def _status():
        return Prompt.objects.get(pk=prompt.pk).status

    assert await _status() == Prompt.StatusChoices.ANSWERED


# ---------------------------------------------------------------------------
# handle_message — no pending prompt → dispatch_text called (chat path)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_message_no_prompt_dispatches_chat(monkeypatch):
    """GIVEN no pending prompt WHEN message arrives THEN dispatch_text is called."""
    room_id = "!chat:example.org"

    dispatched = []

    async def fake_dispatch(thread, text, *, on_event):
        dispatched.append((thread.id, text))
        await on_event({"type": "message_complete", "text": "pong"})

    monkeypatch.setattr("apps.matrix.service.dispatch_text", fake_dispatch)

    send, sent = await _async_send()
    await handle_message(room_id, "@user:example.org", "ping", send=send)

    assert len(dispatched) == 1
    assert dispatched[0][1] == "ping"
    assert len(sent) == 1
    assert sent[0][1] == "pong"
