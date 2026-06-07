"""Pure (no-DB) tests for apps/prompts/render.py."""

from unittest.mock import MagicMock

from apps.prompts.models import Prompt
from apps.prompts.render import parse_reply, render_prompt

# ---------------------------------------------------------------------------
# Helpers — build a minimal Prompt-like object without hitting the DB
# ---------------------------------------------------------------------------


def _prompt(prompt_type, question="Q?", body="", options=None, trust_class=None):
    p = MagicMock(spec=Prompt)
    p.prompt_type = prompt_type
    p.question = question
    p.body = body
    p.options = options if options is not None else []
    p.trust_class = trust_class or Prompt.TrustClass.DECISION
    return p


_OPT_YES_NO = [{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}]
_OPT_ABC = [
    {"key": "a", "label": "Alpha"},
    {"key": "b", "label": "Beta"},
    {"key": "c", "label": "Gamma"},
]


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompt:
    def test_notice_returns_question_only(self):
        """GIVEN NOTICE prompt WHEN render_prompt THEN returns question (no list)."""
        p = _prompt(Prompt.PromptType.NOTICE, question="FYI: job done")
        result = render_prompt(p)
        assert "FYI: job done" in result
        assert "Reply" not in result

    def test_notice_with_body_includes_body(self):
        """GIVEN NOTICE prompt with body WHEN render_prompt THEN body is included."""
        p = _prompt(Prompt.PromptType.NOTICE, question="Notice", body="Details here.")
        result = render_prompt(p)
        assert "Details here." in result

    def test_choice_single_numbered_list(self):
        """GIVEN CHOICE_SINGLE WHEN render_prompt THEN options numbered 1-N + instruction."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        result = render_prompt(p)
        assert "1. Yes" in result
        assert "2. No" in result
        assert "Reply with the number." in result

    def test_choice_multi_numbered_list(self):
        """GIVEN CHOICE_MULTI WHEN render_prompt THEN numbered list + comma instruction."""
        p = _prompt(Prompt.PromptType.CHOICE_MULTI, options=_OPT_ABC)
        result = render_prompt(p)
        assert "1. Alpha" in result
        assert "2. Beta" in result
        assert "3. Gamma" in result
        assert "commas" in result

    def test_approval_default_options_when_empty(self):
        """GIVEN APPROVAL with no options WHEN render_prompt THEN shows Allow/Deny."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        result = render_prompt(p)
        assert "Allow" in result
        assert "Deny" in result
        assert "Reply with the number." in result

    def test_approval_custom_options_numbered(self):
        """GIVEN APPROVAL with custom options WHEN render_prompt THEN custom labels numbered."""
        opts = [{"key": "approve", "label": "Approve"}, {"key": "reject", "label": "Reject"}]
        p = _prompt(Prompt.PromptType.APPROVAL, options=opts)
        result = render_prompt(p)
        assert "1. Approve" in result
        assert "2. Reject" in result

    def test_free_text_instruction(self):
        """GIVEN FREE_TEXT WHEN render_prompt THEN instruction to reply with answer."""
        p = _prompt(Prompt.PromptType.FREE_TEXT, question="What is the ticket?")
        result = render_prompt(p)
        assert "What is the ticket?" in result
        assert "Reply with your answer." in result


# ---------------------------------------------------------------------------
# parse_reply
# ---------------------------------------------------------------------------


class TestParseReply:
    # --- CHOICE_SINGLE ---

    def test_choice_single_number(self):
        """GIVEN CHOICE_SINGLE WHEN reply is '1' THEN maps to first option key."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        assert parse_reply(p, "1") == {"option_keys": ["yes"]}

    def test_choice_single_number_second(self):
        """GIVEN CHOICE_SINGLE WHEN reply is '2' THEN maps to second option key."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        assert parse_reply(p, "2") == {"option_keys": ["no"]}

    def test_choice_single_label_match(self):
        """GIVEN CHOICE_SINGLE WHEN reply is exact label (case-insensitive) THEN maps."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        assert parse_reply(p, "yes") == {"option_keys": ["yes"]}
        assert parse_reply(p, "YES") == {"option_keys": ["yes"]}

    def test_choice_single_garbage_returns_none(self):
        """GIVEN CHOICE_SINGLE WHEN reply is garbage THEN returns None."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        assert parse_reply(p, "maybe") is None

    def test_choice_single_out_of_range_returns_none(self):
        """GIVEN CHOICE_SINGLE WHEN number out of range THEN returns None."""
        p = _prompt(Prompt.PromptType.CHOICE_SINGLE, options=_OPT_YES_NO)
        assert parse_reply(p, "99") is None

    # --- CHOICE_MULTI ---

    def test_choice_multi_comma_numbers(self):
        """GIVEN CHOICE_MULTI WHEN reply is '1,3' THEN maps to keys [a, c]."""
        p = _prompt(Prompt.PromptType.CHOICE_MULTI, options=_OPT_ABC)
        assert parse_reply(p, "1,3") == {"option_keys": ["a", "c"]}

    def test_choice_multi_comma_with_spaces(self):
        """GIVEN CHOICE_MULTI WHEN reply is '1, 2' (with space) THEN maps correctly."""
        p = _prompt(Prompt.PromptType.CHOICE_MULTI, options=_OPT_ABC)
        assert parse_reply(p, "1, 2") == {"option_keys": ["a", "b"]}

    def test_choice_multi_single_valid_number(self):
        """GIVEN CHOICE_MULTI WHEN reply is a single valid number THEN maps to [key]."""
        p = _prompt(Prompt.PromptType.CHOICE_MULTI, options=_OPT_ABC)
        assert parse_reply(p, "2") == {"option_keys": ["b"]}

    def test_choice_multi_partial_garbage_returns_none(self):
        """GIVEN CHOICE_MULTI WHEN one part of comma list is garbage THEN None."""
        p = _prompt(Prompt.PromptType.CHOICE_MULTI, options=_OPT_ABC)
        assert parse_reply(p, "1,xyz") is None

    # --- APPROVAL ---

    def test_approval_number_1_maps_to_allow(self):
        """GIVEN APPROVAL (default Allow/Deny) WHEN reply '1' THEN maps to allow."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "1") == {"option_keys": ["allow"]}

    def test_approval_number_2_maps_to_deny(self):
        """GIVEN APPROVAL WHEN reply '2' THEN maps to deny."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "2") == {"option_keys": ["deny"]}

    def test_approval_alias_allow(self):
        """GIVEN APPROVAL WHEN reply 'allow' THEN maps to allow."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "allow") == {"option_keys": ["allow"]}

    def test_approval_alias_approve(self):
        """GIVEN APPROVAL WHEN reply 'approve' THEN maps to allow key."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "approve") == {"option_keys": ["allow"]}

    def test_approval_alias_deny(self):
        """GIVEN APPROVAL WHEN reply 'deny' THEN maps to deny."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "deny") == {"option_keys": ["deny"]}

    def test_approval_alias_reject(self):
        """GIVEN APPROVAL WHEN reply 'reject' THEN maps to deny key."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "reject") == {"option_keys": ["deny"]}

    def test_approval_garbage_returns_none(self):
        """GIVEN APPROVAL WHEN reply is garbage THEN returns None."""
        p = _prompt(Prompt.PromptType.APPROVAL, options=[])
        assert parse_reply(p, "maybe") is None

    # --- FREE_TEXT ---

    def test_free_text_returns_text_dict(self):
        """GIVEN FREE_TEXT WHEN any text THEN returns {'text': <stripped>}."""
        p = _prompt(Prompt.PromptType.FREE_TEXT)
        assert parse_reply(p, "  hello world  ") == {"text": "hello world"}

    def test_free_text_empty_returns_none(self):
        """GIVEN FREE_TEXT WHEN blank reply THEN returns None."""
        p = _prompt(Prompt.PromptType.FREE_TEXT)
        assert parse_reply(p, "   ") is None

    # --- NOTICE ---

    def test_notice_always_returns_none(self):
        """GIVEN NOTICE prompt WHEN any text THEN returns None (no reply expected)."""
        p = _prompt(Prompt.PromptType.NOTICE)
        assert parse_reply(p, "ok") is None
