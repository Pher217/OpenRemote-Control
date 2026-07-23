"""Tests for apps.setup.telegram_flow and the /telegram/token, /telegram/discover
views: token validation (getMe), chat discovery (getUpdates), token redaction,
and the env-file side effects each view is responsible for.
"""

from __future__ import annotations

import httpx
import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.setup import telegram_flow
from apps.setup.env_writer import read_env
from apps.setup.models import SetupState, SetupToken

TOKEN_URL = "/api/setup/telegram/token"
DISCOVER_URL = "/api/setup/telegram/discover"

RAW_TOKEN = "123456:AAFAKE-token-value-for-tests"


@pytest.fixture
def client():
    return APIClient()


def _live_token() -> str:
    _obj, raw = SetupToken.issue()
    return raw


def _set_challenge(code: str = "ORC-ABC234") -> None:
    """Pin SetupState.telegram_challenge to a known code so a discovery message
    built with the default text (``CHALLENGE``) will match it."""
    state = SetupState.load()
    state.telegram_challenge = code
    state.save(update_fields=["telegram_challenge"])


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client — supports the `with ... as client` usage
    and records the URL of each GET so tests can assert on request shape."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        self.urls.append(url)
        return self._responses.pop(0)


def _patch_client(monkeypatch, *payloads):
    fake = _FakeClient([_FakeResponse(p) for p in payloads])
    monkeypatch.setattr(telegram_flow.httpx, "Client", lambda *a, **k: fake)
    return fake


def _raise_http_error(monkeypatch):
    class _RaisingClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(telegram_flow.httpx, "Client", lambda *a, **k: _RaisingClient())


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


class TestRedact:
    def test_token_is_replaced_with_stars(self):
        """
        GIVEN a message embedding the raw token
        WHEN redact is called
        THEN the token substring is absent and "***" is present
        """
        message = f"GET https://api.telegram.org/bot{RAW_TOKEN}/getMe timed out"
        result = telegram_flow.redact(message, RAW_TOKEN)
        assert RAW_TOKEN not in result
        assert "***" in result

    def test_empty_token_leaves_text_unchanged(self):
        """
        GIVEN an empty token
        WHEN redact is called
        THEN the text is returned unchanged
        """
        assert telegram_flow.redact("no token here", "") == "no token here"


# ---------------------------------------------------------------------------
# get_me
# ---------------------------------------------------------------------------


class TestGetMe:
    def test_ok_response_returns_username_and_id(self, monkeypatch):
        """
        GIVEN Telegram responds ok=true with a username and id
        WHEN get_me is called
        THEN the parsed dict is returned
        """
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 42}})
        result = telegram_flow.get_me(RAW_TOKEN)
        assert result == {"username": "orcbot", "id": 42}

    def test_ok_false_raises_telegram_error(self, monkeypatch):
        """
        GIVEN Telegram responds ok=false (invalid token)
        WHEN get_me is called
        THEN TelegramError is raised
        """
        _patch_client(monkeypatch, {"ok": False, "description": "Not Found"})
        with pytest.raises(telegram_flow.TelegramError):
            telegram_flow.get_me(RAW_TOKEN)

    def test_ok_false_error_message_does_not_contain_the_token(self, monkeypatch):
        """
        GIVEN Telegram responds ok=false with a description embedding the token
             (as Telegram's own error bodies sometimes do)
        WHEN get_me is called
        THEN the raised message does not contain the raw token
        """
        _patch_client(
            monkeypatch,
            {"ok": False, "description": f"bot{RAW_TOKEN} not found"},
        )
        with pytest.raises(telegram_flow.TelegramError) as exc_info:
            telegram_flow.get_me(RAW_TOKEN)
        assert RAW_TOKEN not in str(exc_info.value)

    def test_network_error_raises_telegram_error(self, monkeypatch):
        """
        GIVEN the HTTP call raises httpx.HTTPError
        WHEN get_me is called
        THEN TelegramError is raised
        """
        _raise_http_error(monkeypatch)
        with pytest.raises(telegram_flow.TelegramError):
            telegram_flow.get_me(RAW_TOKEN)

    def test_network_error_does_not_leak_token_via_the_traceback_chain(self, monkeypatch):
        """
        GIVEN a network error whose httpx exception __str__ embeds the request
             URL (and the URL embeds the bot token)
        WHEN get_me raises TelegramError with `from None`
        THEN neither the message nor the full formatted traceback (which a
             logger.exception / Sentry reporter would emit) contains the token —
             the token-bearing exception must not survive as __cause__/__context__
        """
        import traceback

        _raise_http_error(monkeypatch)
        with pytest.raises(telegram_flow.TelegramError) as exc_info:
            telegram_flow.get_me(RAW_TOKEN)
        exc = exc_info.value
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert RAW_TOKEN not in rendered

    def test_missing_username_raises_telegram_error(self, monkeypatch):
        """
        GIVEN Telegram responds ok=true but without a username
        WHEN get_me is called
        THEN TelegramError is raised
        """
        _patch_client(monkeypatch, {"ok": True, "result": {"id": 42}})
        with pytest.raises(telegram_flow.TelegramError):
            telegram_flow.get_me(RAW_TOKEN)


# ---------------------------------------------------------------------------
# discover_chat
# ---------------------------------------------------------------------------


#: The challenge code the wizard mints and the operator echoes into their group.
#: discover_chat now accepts a message only if its text contains this code.
CHALLENGE = "ORC-ABC234"


def _update(chat_type, chat_id, *, title="", is_forum=False, from_id=None, text=CHALLENGE):
    """Build a getUpdates entry. ``text`` defaults to the valid challenge; pass
    a different string (or ``None``) to model a message that does not carry it."""
    chat = {"id": chat_id, "type": chat_type, "title": title, "is_forum": is_forum}
    msg = {"chat": chat}
    if text is not None:
        msg["text"] = text
    if from_id is not None:
        msg["from"] = {"id": from_id}
    return {"message": msg}


class TestDiscoverChat:
    def test_finds_a_supergroup(self, monkeypatch):
        """
        GIVEN getUpdates returns one supergroup message carrying the challenge
        WHEN discover_chat is called with that challenge
        THEN the chat_id, title, is_forum and sender id are captured
        """
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [_update("supergroup", -100123, title="Ops", is_forum=True, from_id=7)],
            },
        )
        found = telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        assert found == {
            "chat_id": "-100123",
            "title": "Ops",
            "is_forum": True,
            "user_id": "7",
        }

    def test_ignores_private_chats(self, monkeypatch):
        """
        GIVEN getUpdates returns only a private-chat message with the challenge
        WHEN discover_chat is called
        THEN None is returned — only group/supergroup chats qualify
        """
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("private", 555, from_id=7)]},
        )
        assert telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE) is None

    def test_no_updates_returns_none(self, monkeypatch):
        """
        GIVEN getUpdates returns an empty result list
        WHEN discover_chat is called
        THEN None is returned
        """
        _patch_client(monkeypatch, {"ok": True, "result": []})
        assert telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE) is None

    def test_message_without_the_challenge_is_ignored(self, monkeypatch):
        """
        GIVEN a valid group message whose text does NOT contain the challenge
        WHEN discover_chat is called
        THEN None is returned — an unchallenged group must never be adopted
        """
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, from_id=1, text="hello there")]},
        )
        assert telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE) is None

    def test_wrong_challenge_is_ignored(self, monkeypatch):
        """
        GIVEN a group message carrying a DIFFERENT challenge code
        WHEN discover_chat is called with the real challenge
        THEN None is returned
        """
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, from_id=1, text="ORC-ZZZZZZ")]},
        )
        assert telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE) is None

    def test_challenge_match_is_case_insensitive_and_whitespace_tolerant(self, monkeypatch):
        """
        GIVEN the operator's message wraps the challenge in other text and case
        WHEN discover_chat is called
        THEN the chat is still matched — the code is compared case-folded and
             as a substring, so surrounding words do not defeat it
        """
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [_update("group", -7, from_id=3, text=f"  here: {CHALLENGE.lower()} ")],
            },
        )
        found = telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        assert found["chat_id"] == "-7"

    def test_first_matching_update_wins(self, monkeypatch):
        """
        GIVEN two group messages BOTH carrying the challenge, from different chats
        WHEN discover_chat is called
        THEN the FIRST matching update's chat is returned, not the last —
             with the challenge as the gate, "first" removes an attacker's
             "I message last" advantage rather than granting it
        """
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [
                    _update("group", -1, title="First", from_id=1),
                    _update("supergroup", -2, title="Second", from_id=2),
                ],
            },
        )
        found = telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        assert found["chat_id"] == "-1"
        assert found["title"] == "First"

    def test_challenge_bearing_message_wins_over_earlier_decoys(self, monkeypatch):
        """
        GIVEN an attacker's un-challenged group message arrives BEFORE the
             operator's challenge-bearing one
        WHEN discover_chat is called
        THEN the operator's chat is chosen — arriving first buys the attacker
             nothing without the code
        """
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [
                    _update("supergroup", -666, title="Attacker", from_id=99, text="let me in"),
                    _update("group", -1, title="Operator", from_id=1),
                ],
            },
        )
        found = telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        assert found["chat_id"] == "-1"
        assert found["user_id"] == "1"

    def test_message_without_sender_is_ignored(self, monkeypatch):
        """
        GIVEN a group message that carries the challenge but has no "from" id
        WHEN discover_chat is called
        THEN None is returned — a match with no sender id cannot populate the
             default-deny allowlist, so it is skipped rather than half-adopted
        """
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, title="Ops", from_id=None)]},
        )
        assert telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE) is None

    def test_conflict_409_becomes_an_actionable_stop_the_bot_message(self, monkeypatch):
        """
        GIVEN Telegram answers getUpdates with a 409 "terminated by other
             getUpdates request" (the live bot is already long-polling)
        WHEN discover_chat is called
        THEN TelegramError carries the instruction to stop the running bot, not
             the raw conflict string that reads as a transient glitch
        """
        fake = _FakeClient(
            [
                _FakeResponse(
                    {
                        "ok": False,
                        "description": "Conflict: terminated by other getUpdates request",
                    },
                    status_code=409,
                )
            ]
        )
        monkeypatch.setattr(telegram_flow.httpx, "Client", lambda *a, **k: fake)
        with pytest.raises(telegram_flow.TelegramError) as exc_info:
            telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        message = str(exc_info.value)
        assert "Stop the running OpenRemote-Control bot" in message

    def test_request_url_targets_the_bot_getupdates_endpoint(self, monkeypatch):
        """
        GIVEN a discovery call
        WHEN discover_chat issues the HTTP request
        THEN the URL is the bot-scoped getUpdates endpoint carrying the token —
             proving the token is placed in the path, which is exactly why the
             token regex must forbid path-breaking characters
        """
        fake = _patch_client(monkeypatch, {"ok": True, "result": []})
        telegram_flow.discover_chat(RAW_TOKEN, CHALLENGE)
        assert fake.urls == [f"https://api.telegram.org/bot{RAW_TOKEN}/getUpdates"]


# ---------------------------------------------------------------------------
# POST /telegram/token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTelegramTokenView:
    def test_valid_token_writes_it_to_the_env_file(self, client, monkeypatch, tmp_path):
        """
        GIVEN Telegram validates the token via getMe
        WHEN POST /telegram/token is sent with that token
        THEN TELEGRAM_BOT_TOKEN is written to the env file
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 200
            assert read_env(env_path)["TELEGRAM_BOT_TOKEN"] == RAW_TOKEN

    def test_valid_token_response_does_not_contain_the_token(self, client, monkeypatch, tmp_path):
        """
        GIVEN a valid token
        WHEN POST /telegram/token succeeds
        THEN the response body does not echo the raw token back
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert RAW_TOKEN not in resp.content.decode()

    def test_valid_token_returns_bot_link(self, client, monkeypatch, tmp_path):
        """
        GIVEN a valid token
        WHEN POST /telegram/token succeeds
        THEN the response includes the username and a t.me bot link
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            body = resp.json()
            assert body["username"] == "orcbot"
            assert body["bot_link"] == "https://t.me/orcbot"

    def test_valid_token_returns_and_persists_a_challenge_code(self, client, monkeypatch, tmp_path):
        """
        GIVEN a valid token
        WHEN POST /telegram/token succeeds
        THEN the response carries a fresh ORC- challenge, and the same code is
             persisted on SetupState so discovery can require it
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            challenge = resp.json()["challenge"]
            assert challenge.startswith("ORC-")
            assert SetupState.load().telegram_challenge == challenge

    def test_rejected_when_wizard_has_left_the_providers_stage(self, client, monkeypatch, tmp_path):
        """
        GIVEN the wizard has advanced past the providers stage
        WHEN POST /telegram/token is sent
        THEN 409 is returned and no token is written — provider config must not
             be accepted once the operator has left that screen
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            state = SetupState.load()
            state.set_provider("telegram", "connected")
            state.advance_to(SetupState.STAGE_RUNTIMES)
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 409
            assert "TELEGRAM_BOT_TOKEN" not in read_env(env_path)

    def test_invalid_token_returns_400_without_leaking_the_token(
        self, client, monkeypatch, tmp_path
    ):
        """
        GIVEN Telegram rejects the token (ok=false)
        WHEN POST /telegram/token is sent
        THEN 400 is returned and the response body does not contain the token
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": False, "description": "Unauthorized"})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 400
            assert RAW_TOKEN not in resp.content.decode()

    def test_token_with_surrounding_whitespace_is_stripped_before_use(
        self, client, monkeypatch, tmp_path
    ):
        """
        GIVEN a token pasted with surrounding whitespace/newline
        WHEN POST /telegram/token is sent
        THEN the stripped token (no whitespace) is what gets written
        """
        env_path = tmp_path / ".env"
        _patch_client(monkeypatch, {"ok": True, "result": {"username": "orcbot", "id": 1}})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": f"  {RAW_TOKEN}\n"},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 200
            assert read_env(env_path)["TELEGRAM_BOT_TOKEN"] == RAW_TOKEN

    def test_empty_token_after_stripping_returns_400(self, client, tmp_path):
        """
        GIVEN a token that is only whitespace
        WHEN POST /telegram/token is sent
        THEN 400 is returned
        """
        env_path = tmp_path / ".env"
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": "   "},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 400

    def test_requires_a_valid_setup_token(self, client, tmp_path):
        """
        GIVEN no setup token is supplied
        WHEN POST /telegram/token is sent
        THEN 403 is returned
        """
        env_path = tmp_path / ".env"
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            resp = client.post(
                TOKEN_URL, {"token": RAW_TOKEN}, format="json", HTTP_HOST="localhost"
            )
            assert resp.status_code == 403

    def test_returns_410_once_setup_is_complete(self, client, tmp_path):
        """
        GIVEN setup has already been completed
        WHEN POST /telegram/token is sent with a fresh valid token
        THEN 410 is returned
        """
        env_path = tmp_path / ".env"
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            state = SetupState.load()
            state.set_provider("telegram", "connected")
            state.advance_to(SetupState.STAGE_RUNTIMES)
            state.advance_to(SetupState.STAGE_DONE)
            raw = _live_token()
            resp = client.post(
                TOKEN_URL,
                {"token": RAW_TOKEN},
                format="json",
                HTTP_HOST="localhost",
                HTTP_X_ORC_SETUP_TOKEN=raw,
            )
            assert resp.status_code == 410


# ---------------------------------------------------------------------------
# POST /telegram/discover
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTelegramDiscoverView:
    def test_no_token_configured_returns_400(self, client, tmp_path):
        """
        GIVEN the env file has no TELEGRAM_BOT_TOKEN
        WHEN POST /telegram/discover is sent
        THEN 400 is returned
        """
        env_path = tmp_path / ".env"
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 400

    def test_nothing_seen_yet_returns_200_found_false(self, client, monkeypatch, tmp_path):
        """
        GIVEN a configured token but no group message has been sent yet
        WHEN POST /telegram/discover is sent
        THEN 200 is returned with found: false — this is normal polling, not
             an error
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(monkeypatch, {"ok": True, "result": []})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 200
            assert resp.json() == {"found": False}

    def test_nothing_seen_yet_does_not_mark_provider_connected(self, client, monkeypatch, tmp_path):
        """
        GIVEN a configured token but no group message has been sent yet
        WHEN POST /telegram/discover returns found: false
        THEN SetupState.load().connected_providers is still empty
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(monkeypatch, {"ok": True, "result": []})
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert SetupState.load().connected_providers == []

    def test_success_writes_all_keys_and_marks_provider_connected(
        self, client, monkeypatch, tmp_path
    ):
        """
        GIVEN a configured token and a group message with a sender
        WHEN POST /telegram/discover finds the chat
        THEN TELEGRAM_FORUM_CHAT_ID, ORC_PROMPT_CHAT_ID, ORC_MESSAGING_PLATFORM
             and TELEGRAM_ALLOWED_CHAT_IDS are all written, and the provider is
             marked connected
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [_update("supergroup", -100999, title="Ops", is_forum=True, from_id=42)],
            },
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 200
            env = read_env(env_path)
            assert env["TELEGRAM_FORUM_CHAT_ID"] == "-100999"
            assert env["ORC_PROMPT_CHAT_ID"] == "-100999"
            assert env["ORC_MESSAGING_PLATFORM"] == "telegram"
            assert env["TELEGRAM_ALLOWED_CHAT_IDS"] == "42"
            assert SetupState.load().connected_providers == ["telegram"]

    def test_success_burns_the_challenge(self, client, monkeypatch, tmp_path):
        """
        GIVEN discovery succeeds
        WHEN the chat is matched and written
        THEN the challenge is cleared from SetupState — it is single-use, so a
             later replay of the same code (now public in the group) cannot
             satisfy a re-run
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, title="Ops", from_id=5)]},
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert SetupState.load().telegram_challenge == ""

    def test_no_challenge_on_record_returns_400(self, client, monkeypatch, tmp_path):
        """
        GIVEN a token is configured but no challenge has been minted (discovery
             reached out of order, before token validation)
        WHEN POST /telegram/discover is sent
        THEN 400 is returned and getUpdates is never called — the flow fails
             closed rather than adopting the first group message it sees
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        fake = _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, title="Ops", from_id=5)]},
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 400
            assert fake.urls == []

    def test_success_response_surfaces_is_forum(self, client, monkeypatch, tmp_path):
        """
        GIVEN a discovered chat that has Topics disabled (is_forum: false)
        WHEN POST /telegram/discover finds it
        THEN the response reports is_forum: false rather than failing
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [_update("group", -55, title="Plain", is_forum=False, from_id=9)],
            },
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["found"] is True
            assert body["is_forum"] is False

    def test_message_without_sender_is_not_adopted(self, client, monkeypatch, tmp_path):
        """
        GIVEN the only challenge-bearing group message has no "from" id
        WHEN POST /telegram/discover is sent
        THEN the response is found: false and no chat/allowlist keys are written
             — discover_chat skips senderless matches, so nothing is adopted
             behind an unpopulated default-deny allowlist
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -55, title="Plain", from_id=None)]},
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.json() == {"found": False}
            env = read_env(env_path)
            assert "TELEGRAM_ALLOWED_CHAT_IDS" not in env
            assert "TELEGRAM_FORUM_CHAT_ID" not in env
            assert SetupState.load().connected_providers == []

    def test_attacker_message_without_the_challenge_is_ignored(self, client, monkeypatch, tmp_path):
        """
        GIVEN an attacker adds the (public-username) bot to their own group and
             messages it during the detection window WITHOUT the challenge code
        WHEN POST /telegram/discover polls getUpdates
        THEN the attacker's chat id never lands in TELEGRAM_ALLOWED_CHAT_IDS —
             the response is found: false and the default-deny allowlist stays
             unwritten. This is the privilege-escalation regression guard.
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        _set_challenge()
        _patch_client(
            monkeypatch,
            {
                "ok": True,
                "result": [
                    _update("supergroup", -666, title="Attacker", from_id=99, text="add me"),
                ],
            },
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.json() == {"found": False}
            assert "TELEGRAM_ALLOWED_CHAT_IDS" not in read_env(env_path)

    def test_rejected_when_wizard_has_left_the_providers_stage(self, client, monkeypatch, tmp_path):
        """
        GIVEN the wizard has advanced past the providers stage
        WHEN POST /telegram/discover is sent
        THEN 409 is returned and getUpdates is never polled
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        state = SetupState.load()
        state.set_provider("telegram", "connected")
        state.advance_to(SetupState.STAGE_RUNTIMES)
        fake = _patch_client(
            monkeypatch,
            {"ok": True, "result": [_update("group", -1, title="Ops", from_id=5)]},
        )
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 409
            assert fake.urls == []

    def test_requires_a_valid_setup_token(self, client, tmp_path):
        """
        GIVEN no setup token is supplied
        WHEN POST /telegram/discover is sent
        THEN 401 or 403 is returned
        """
        env_path = tmp_path / ".env"
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            resp = client.post(DISCOVER_URL, format="json", HTTP_HOST="localhost")
            assert resp.status_code == 403

    def test_returns_410_once_setup_is_complete(self, client, tmp_path):
        """
        GIVEN setup has already been completed
        WHEN POST /telegram/discover is sent with a fresh valid token
        THEN 410 is returned
        """
        env_path = tmp_path / ".env"
        env_path.write_text(f"TELEGRAM_BOT_TOKEN='{RAW_TOKEN}'\n", encoding="utf-8")
        with override_settings(ORC_SETUP_ENV_FILE=str(env_path)):
            state = SetupState.load()
            state.set_provider("telegram", "connected")
            state.advance_to(SetupState.STAGE_RUNTIMES)
            state.advance_to(SetupState.STAGE_DONE)
            raw = _live_token()
            resp = client.post(
                DISCOVER_URL, format="json", HTTP_HOST="localhost", HTTP_X_ORC_SETUP_TOKEN=raw
            )
            assert resp.status_code == 410
