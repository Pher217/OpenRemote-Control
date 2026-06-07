"""Tests for the single messaging-app-of-choice routing contract."""

import pytest

from apps.messaging import routing


def test_active_platform_defaults_to_telegram_when_unset(settings):
    """
    GIVEN ORC_MESSAGING_PLATFORM is empty
    WHEN active_platform is resolved
    THEN it defaults to telegram
    """
    settings.ORC_MESSAGING_PLATFORM = ""
    assert routing.active_platform() == "telegram"
    assert routing.is_telegram() is True


def test_active_platform_falls_back_on_unknown_value(settings):
    """
    GIVEN ORC_MESSAGING_PLATFORM is an unrecognised value
    WHEN active_platform is resolved
    THEN it falls back to telegram
    """
    settings.ORC_MESSAGING_PLATFORM = "carrier-pigeon"
    assert routing.active_platform() == "telegram"


def test_active_platform_normalises_case_and_whitespace(settings):
    """
    GIVEN ORC_MESSAGING_PLATFORM has mixed case and surrounding whitespace
    WHEN active_platform is resolved
    THEN it is lowercased and stripped
    """
    settings.ORC_MESSAGING_PLATFORM = "  WhatsApp  "
    assert routing.active_platform() == "whatsapp"
    assert routing.is_telegram() is False


@pytest.mark.parametrize("platform", routing.GATEWAY_PLATFORMS)
def test_gateway_platforms_are_recognised(settings, platform):
    """
    GIVEN ORC_MESSAGING_PLATFORM is each gateway platform
    WHEN active_platform is resolved
    THEN that platform is returned and is_telegram is False
    """
    settings.ORC_MESSAGING_PLATFORM = platform
    assert routing.active_platform() == platform
    assert routing.is_telegram() is False


def test_active_recipient_telegram_uses_prompt_chat_id(settings):
    """
    GIVEN telegram is active and ORC_PROMPT_CHAT_ID is set
    WHEN active_recipient is resolved
    THEN it returns the prompt chat id
    """
    settings.ORC_MESSAGING_PLATFORM = "telegram"
    settings.ORC_PROMPT_CHAT_ID = "-1009999"
    assert routing.active_recipient() == "-1009999"


def test_active_recipient_gateway_uses_platform_setting(settings):
    """
    GIVEN whatsapp is active and ORC_PROMPT_WHATSAPP is set
    WHEN active_recipient is resolved
    THEN it returns the whatsapp recipient and ignores other platform settings
    """
    settings.ORC_MESSAGING_PLATFORM = "whatsapp"
    settings.ORC_PROMPT_WHATSAPP = "41790000000"
    settings.ORC_PROMPT_SLACK = "C123"
    assert routing.active_recipient() == "41790000000"


def test_active_recipient_empty_when_unconfigured(settings):
    """
    GIVEN signal is active but ORC_PROMPT_SIGNAL is empty
    WHEN active_recipient is resolved
    THEN it returns an empty string
    """
    settings.ORC_MESSAGING_PLATFORM = "signal"
    settings.ORC_PROMPT_SIGNAL = ""
    assert routing.active_recipient() == ""
