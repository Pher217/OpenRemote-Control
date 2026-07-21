"""Synchronous Telegram Bot API calls used only by the setup wizard.

Deliberately separate from ``apps.telegram.telegram_api``: that module is
async and reads its bot token from ``settings.TELEGRAM_BOT_TOKEN``, which does
not yet hold the value the operator is validating here. The wizard writes the
token straight to ``deploy/.env``, but Django settings were loaded at process
boot and will not pick up a file changed afterward.
"""

from __future__ import annotations

import httpx

API_BASE = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(15.0)

#: Chat types the wizard treats as "the operator's group".
_GROUP_CHAT_TYPES = ("group", "supergroup")


class TelegramError(RuntimeError):
    """Raised when a Telegram Bot API call fails, on the network or logically."""


def redact(text: str, token: str) -> str:
    """Replace ``token`` with ``***`` in ``text``, when the token is non-empty.

    Every error message and log line that could embed a request URL MUST pass
    through this first — httpx's own error strings embed the URL, and the URL
    embeds the token.
    """
    return text.replace(token, "***") if token else text


def _call(token: str, method: str, params: dict | None = None) -> dict:
    """GET ``{API_BASE}/bot{token}/{method}`` and return the ``result`` payload."""
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(url, params=params)
    except (httpx.HTTPError, httpx.InvalidURL):
        # Break the exception chain deliberately (`from None`): httpx's own
        # exception __str__ embeds the request URL, and the URL embeds the
        # bot token. Keeping `from exc` would leave the token recoverable from
        # traceback.format_exception() output — via logger.exception, a DEBUG
        # error page, or a Sentry-style reporter — even though the message we
        # raise here is clean. The token-bearing exception must not survive
        # as __cause__.
        raise TelegramError("Could not reach Telegram (network error).") from None
    # Not every response on this path is JSON. A corporate proxy, a captive
    # portal, or a Telegram edge returning a 502 HTML page all yield a body
    # that json() rejects with a ValueError — which is NOT an httpx.HTTPError
    # and would otherwise escape as an unhandled 500.
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TelegramError(
            f"Telegram returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc
    if not isinstance(payload, dict):
        raise TelegramError("Telegram returned an unexpected response shape.")
    if payload.get("ok") is not True:
        description = str(payload.get("description", "Telegram API error"))
        # getUpdates is single-consumer by contract. When the live bot process
        # is already long-polling, Telegram answers the wizard with HTTP 409
        # "Conflict: terminated by other getUpdates request". Surfaced raw this
        # reads as a transient glitch, so the operator retries forever — the
        # wizard can never win the race against a supervised bot that restarts.
        # Translate it into the one instruction that actually unblocks setup.
        if resp.status_code == 409 or "terminated by other getupdates" in description.lower():
            raise TelegramError(
                "Another process is already receiving this bot's updates. "
                "Stop the running OpenRemote-Control bot before detecting the "
                "group — Docker Compose: `docker compose stop telegram-bot`; "
                "launchd: `launchctl bootout gui/$(id -u)/com.openremote.bot` "
                "— then try again."
            ) from None
        # Telegram's own error descriptions never contain the token, so
        # normal chaining is fine here — only the network-error path above
        # carries a token-bearing exception.
        raise TelegramError(redact(description, token))
    if "result" not in payload:
        raise TelegramError("Telegram returned an unexpected response shape.")
    return payload["result"]


def get_me(token: str) -> dict:
    """Validate ``token`` via getMe. Returns ``{"username", "id"}``."""
    result = _call(token, "getMe")
    if not isinstance(result, dict):
        raise TelegramError("Telegram returned an unexpected response shape.")
    username = result.get("username")
    if not username:
        raise TelegramError("Telegram did not return a bot username.")
    return {"username": username, "id": result.get("id")}


def discover_chat(token: str, challenge: str) -> dict | None:
    """Poll getUpdates for a group message containing ``challenge``.

    This deliberately diverges from the bash loop in quickstart.sh, which
    accepted ANY group message and let the LAST one win. Telegram bot
    usernames are public, so anyone can add the bot to their own group and
    message it during the discovery window — with no challenge, the fastest
    attacker's chat/user id would land in TELEGRAM_ALLOWED_CHAT_IDS, a
    default-deny allowlist, granting them the ability to drive coding-agent
    sessions on this machine.

    An update is accepted only when ALL of:
      - it is a message (or channel post) in a "group"/"supergroup" chat
        with a usable chat id
      - the message text or caption contains ``challenge``, compared
        case-insensitively with whitespace stripped from both sides
      - the sender carries a numeric ``from.id``

    The FIRST matching update wins, not the last — with the challenge as the
    gate, "first" is the correct choice: it is the earliest proof that
    someone who can see the wizard page also posted in the group, and taking
    a later update instead would just reopen the same race to whichever
    message arrives last.

    Returns ``None`` when no such message has been seen yet (the normal
    "keep polling" case, not an error).
    """
    result = _call(token, "getUpdates")
    if not isinstance(result, list):
        raise TelegramError("Telegram returned an unexpected response shape.")
    needle = challenge.strip().lower()
    for update in result:
        if not isinstance(update, dict):
            continue
        message = update.get("message") or update.get("channel_post") or {}
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        if not isinstance(chat, dict):
            continue
        if chat.get("type") not in _GROUP_CHAT_TYPES:
            continue
        # Everything here is remote input; a chat without an id is unusable
        # and must be skipped rather than raising KeyError into a 500.
        if chat.get("id") is None:
            continue
        text = message.get("text") or message.get("caption") or ""
        if not isinstance(text, str) or needle not in text.strip().lower():
            continue
        sender = message.get("from") or {}
        if not isinstance(sender, dict) or not sender.get("id"):
            continue
        return {
            "chat_id": str(chat["id"]),
            "title": chat.get("title", ""),
            "is_forum": bool(chat.get("is_forum")),
            "user_id": str(sender["id"]),
        }
    return None
