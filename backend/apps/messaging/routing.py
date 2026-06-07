"""The single messaging app of choice.

OpenRemote-Control is a single-platform aggregator: the operator picks ONE
messaging app and every session and prompt they follow flows to it. Pick
Telegram and it's a Telegram-only aggregator; pick WhatsApp and everything goes
to WhatsApp. Nothing is broadcast to multiple platforms.

This module is the routing contract shared by the connector delivery path
(apps.connectors) and the session-observer path (apps.observe). It resolves the
active platform and its recipient/chat id from settings; callers switch between
the native Telegram surface and the gateway outbox on that single answer.
"""

from __future__ import annotations

from django.conf import settings

TELEGRAM = "telegram"

# Gateway platforms reach the user via the Node messaging-gateway sidecar
# (apps.gateway outbox). Telegram is delivered natively (apps.telegram).
GATEWAY_PLATFORMS = ("whatsapp", "slack", "discord", "signal", "imessage")

_RECIPIENT_SETTING = {
    "whatsapp": "ORC_PROMPT_WHATSAPP",
    "slack": "ORC_PROMPT_SLACK",
    "discord": "ORC_PROMPT_DISCORD",
    "signal": "ORC_PROMPT_SIGNAL",
    "imessage": "ORC_PROMPT_IMESSAGE",
}

VALID_PLATFORMS = (TELEGRAM, *GATEWAY_PLATFORMS)


def active_platform() -> str:
    """Return the single configured messaging platform.

    Falls back to ``telegram`` when unset or set to an unknown value.
    """
    p = (getattr(settings, "ORC_MESSAGING_PLATFORM", "") or TELEGRAM).strip().lower()
    return p if p in VALID_PLATFORMS else TELEGRAM


def is_telegram() -> bool:
    """True when the active platform is delivered natively via Telegram."""
    return active_platform() == TELEGRAM


def active_recipient() -> str:
    """Return the recipient/chat id for the active platform, or '' if unconfigured.

    For Telegram this is ``ORC_PROMPT_CHAT_ID`` (which itself falls back to
    ``TELEGRAM_FORUM_CHAT_ID``). For a gateway platform it is that platform's
    ``ORC_PROMPT_<PLATFORM>`` setting.
    """
    platform = active_platform()
    if platform == TELEGRAM:
        return getattr(settings, "ORC_PROMPT_CHAT_ID", "") or getattr(
            settings, "TELEGRAM_FORUM_CHAT_ID", ""
        )
    return getattr(settings, _RECIPIENT_SETTING[platform], "")
