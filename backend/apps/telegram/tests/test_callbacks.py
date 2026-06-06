from datetime import timedelta

import pytest
from channels.db import database_sync_to_async
from django.utils import timezone

from apps.accounts.models import Account
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt
from apps.prompts.surfaces.telegram import build_reply_markup, parse_callback
from apps.telegram.service import handle_callback_query
from apps.threads.models import Thread


def make_account(suffix="cb1"):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-cb-{suffix}",
        credential_recipient=f"r-cb-{suffix}",
    )


def make_thread(account, name="cb-test"):
    return Thread.objects.create(
        name=name,
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        account=account,
    )


# ---------------------------------------------------------------------------
# build_reply_markup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildReplyMarkup:
    def test_approval_default_buttons(self):
        """GIVEN APPROVAL prompt with no options WHEN build_reply_markup THEN 3 default buttons."""
        account = make_account("bm1")
        thread = make_thread(account, "bm-thread-1")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Deploy?",
        )
        markup = build_reply_markup(prompt)
        assert markup is not None
        buttons = [row[0] for row in markup["inline_keyboard"]]
        labels = [b["text"] for b in buttons]
        assert "Approve" in labels
        assert "Reject" in labels
        assert "Defer" in labels

    def test_choice_single_one_button_per_option(self):
        """GIVEN CHOICE_SINGLE prompt WHEN build_reply_markup THEN one button per option."""
        account = make_account("bm2")
        thread = make_thread(account, "bm-thread-2")
        options = [
            {"key": "yes", "label": "Yes"},
            {"key": "no", "label": "No"},
        ]
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_SINGLE,
            question="Proceed?",
            options=options,
        )
        markup = build_reply_markup(prompt)
        assert markup is not None
        assert len(markup["inline_keyboard"]) == 2

    def test_choice_multi_has_confirm_button(self):
        """GIVEN CHOICE_MULTI prompt WHEN build_reply_markup THEN confirm button appended."""
        account = make_account("bm3")
        thread = make_thread(account, "bm-thread-3")
        options = [
            {"key": "opt_a", "label": "Option A"},
            {"key": "opt_b", "label": "Option B"},
        ]
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.CHOICE_MULTI,
            question="Pick all that apply",
            options=options,
        )
        markup = build_reply_markup(prompt)
        assert markup is not None
        all_buttons = [row[0] for row in markup["inline_keyboard"]]
        confirm = [b for b in all_buttons if b["callback_data"].endswith(":__confirm")]
        assert len(confirm) == 1

    def test_notice_returns_none(self):
        """GIVEN NOTICE prompt WHEN build_reply_markup THEN None (no keyboard)."""
        account = make_account("bm4")
        thread = make_thread(account, "bm-thread-4")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.NOTICE,
            question="FYI: session started",
        )
        assert build_reply_markup(prompt) is None

    def test_free_text_returns_none(self):
        """GIVEN FREE_TEXT prompt WHEN build_reply_markup THEN None."""
        account = make_account("bm5")
        thread = make_thread(account, "bm-thread-5")
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.FREE_TEXT,
            question="Describe the issue",
        )
        assert build_reply_markup(prompt) is None

    def test_callback_data_within_64_bytes(self):
        """GIVEN any prompt with options WHEN build_reply_markup THEN all callback_data <=64 bytes."""
        account = make_account("bm6")
        thread = make_thread(account, "bm-thread-6")
        options = [{"key": "approve", "label": "Approve"}, {"key": "reject", "label": "Reject"}]
        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question="Approve deployment?",
            options=options,
        )
        markup = build_reply_markup(prompt)
        assert markup is not None
        for row in markup["inline_keyboard"]:
            for btn in row:
                cb = btn["callback_data"]
                assert len(cb.encode()) <= 64, f"callback_data too long: {cb!r}"


# ---------------------------------------------------------------------------
# parse_callback
# ---------------------------------------------------------------------------


class TestParseCallback:
    def test_round_trips_valid_data(self):
        """GIVEN well-formed 'p:{nonce}:{key}' WHEN parse_callback THEN returns (nonce, key)."""
        nonce = "abcd1234abcd1234"
        key = "approve"
        data = f"p:{nonce}:{key}"
        result = parse_callback(data)
        assert result == (nonce, key)

    def test_rejects_missing_prefix(self):
        """GIVEN data without 'p:' prefix WHEN parse_callback THEN returns None."""
        assert parse_callback("x:abcd1234abcd1234:yes") is None

    def test_rejects_too_short(self):
        """GIVEN data that is too short to hold nonce+key WHEN parse_callback THEN returns None."""
        assert parse_callback("p:short") is None

    def test_rejects_empty_key(self):
        """GIVEN data with empty key part WHEN parse_callback THEN returns None."""
        assert parse_callback("p:abcd1234abcd1234:") is None

    def test_rejects_garbage(self):
        """GIVEN random garbage WHEN parse_callback THEN returns None."""
        assert parse_callback("not-a-callback") is None

    def test_confirm_key_round_trips(self):
        """GIVEN '__confirm' key WHEN parse_callback THEN returns it correctly."""
        nonce = "1234abcd1234abcd"
        data = f"p:{nonce}:__confirm"
        result = parse_callback(data)
        assert result == (nonce, "__confirm")


# ---------------------------------------------------------------------------
# handle_callback_query (integration)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_callback_query_resolves_pending_prompt(settings):
    """GIVEN a PENDING prompt WHEN handle_callback_query is called with valid data THEN prompt is answered."""
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {99999}

    account = await database_sync_to_async(make_account)("hcq1")
    thread = await database_sync_to_async(make_thread)(account, "hcq-thread-1")
    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question="Allow action?",
        options=[{"key": "approve", "label": "Approve"}, {"key": "reject", "label": "Reject"}],
    )

    acked = []

    async def fake_answer(cq_id, text="", show_alert=False):
        acked.append({"id": cq_id, "text": text})

    data = f"p:{prompt.nonce}:approve"
    await handle_callback_query("cq-id-1", 99999, data, answer=fake_answer)

    assert len(acked) == 1
    assert acked[0]["id"] == "cq-id-1"
    assert "Recorded" in acked[0]["text"]

    @database_sync_to_async
    def _status():
        p = Prompt.objects.get(pk=prompt.pk)
        return p.status, p.answered_by

    status, answered_by = await _status()
    assert status == Prompt.StatusChoices.ANSWERED
    assert answered_by == "99999"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_callback_query_expired_prompt(settings):
    """GIVEN an expired prompt WHEN handle_callback_query is called THEN acks with expired message."""
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {99999}

    account = await database_sync_to_async(make_account)("hcq2")
    thread = await database_sync_to_async(make_thread)(account, "hcq-thread-2")
    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.APPROVAL,
        question="Allow?",
        options=[{"key": "approve", "label": "Approve"}],
    )

    @database_sync_to_async
    def _expire():
        Prompt.objects.filter(pk=prompt.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    await _expire()

    acked = []

    async def fake_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    await handle_callback_query("cq-id-2", 99999, f"p:{prompt.nonce}:approve", answer=fake_answer)

    assert acked
    assert "Expired" in acked[0] or "already" in acked[0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_callback_query_not_allowed(settings):
    """GIVEN non-allowlisted user WHEN handle_callback_query is called THEN acks with not-authorised."""
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {99999}

    acked = []

    async def fake_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    await handle_callback_query("cq-id-3", 77777, "p:abcd1234abcd1234:approve", answer=fake_answer)

    assert acked
    assert "authorised" in acked[0].lower() or "authorized" in acked[0].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_callback_query_malformed_data(settings):
    """GIVEN malformed callback data WHEN handle_callback_query is called THEN acks with unknown."""
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {99999}

    acked = []

    async def fake_answer(cq_id, text="", show_alert=False):
        acked.append(text)

    await handle_callback_query("cq-id-4", 99999, "garbage-data", answer=fake_answer)

    assert acked
    assert "Unknown" in acked[0] or "unknown" in acked[0]
