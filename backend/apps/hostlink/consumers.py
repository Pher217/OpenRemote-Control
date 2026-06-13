import logging
import time
from pathlib import Path

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.hostlink import security
from apps.hostlink.models import HostToken
from apps.hosts.models import Host
from apps.observe.delivery import deliver_turn
from apps.observe.runtimes import UnknownRuntimeError, get_runtime_adapter
from apps.observe.service import (
    apply_session_meta,
    get_or_create_observed_thread,
    record_turn,
)
from apps.telegram.telegram_api import redact_token
from apps.threads.models import Thread

logger = logging.getLogger(__name__)


class HostDaemonConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for a host-agent daemon.

    Connection URL: ws/hosts/<host_id>/
    Query parameters (all required):
        token     — raw per-host token obtained from the enroll endpoint
        ts        — Unix timestamp (integer seconds, str)
        nonce     — random string, unique per connection attempt (replay window 300 s)
        signature — HMAC-SHA256 of "{host_id}:{ts}:{nonce}" keyed by *token*

    The host identity is resolved server-side from *host_id* in the URL path.
    Any host_id in the payload is ignored — self.host is the authoritative identity.
    """

    async def connect(self):
        host_id = self.scope["url_route"]["kwargs"]["host_id"]
        qs = dict(
            pair.split("=", 1)
            for pair in self.scope.get("query_string", b"").decode().split("&")
            if "=" in pair
        )
        token = qs.get("token", "")
        ts = qs.get("ts", "")
        nonce = qs.get("nonce", "")
        signature = qs.get("signature", "")

        host = await self._get_host(host_id)
        if host is None:
            await self.close(code=4001)
            return

        # Verify the raw token against the stored hash.
        token_ok = await database_sync_to_async(HostToken.verify)(host, token)
        if not token_ok:
            await self.close(code=4001)
            return

        # Verify HMAC signature + timestamp skew (token is the HMAC secret).
        ok, _ = security.verify_sig(
            secret=token,
            host_id=str(host.id),
            ts=ts,
            nonce=nonce,
            signature=signature,
            now=time.time(),
        )
        if not ok:
            await self.close(code=4001)
            return

        # Nonce replay prevention: cache.add returns False if the key already
        # exists (replayed nonce). Uses django.core.cache which must be backed
        # by a shared store (Redis, Memcached) in multi-process deployments.
        # LocMemCache works for single-process / test environments only.
        nonce_key = f"hostnonce:{host.id}:{nonce}"
        accepted = await database_sync_to_async(cache.add)(nonce_key, 1, timeout=300)
        if not accepted:
            await self.close(code=4001)
            return

        self.host = host
        self.group_name = f"host_{host.id}"
        # Per-file remembered session id, for runtimes whose turn lines carry no
        # session id of their own (Codex/Gemini) — mirrors the observer's file_state.
        self._file_sessions: dict[str, str] = {}
        # Per-PTY-session thread id cache: session_name -> str(thread.id)
        self._pty_threads: dict[str, str] = {}
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        if not isinstance(content, dict):
            return
        msg_type = content.get("type")
        if msg_type == "session.event":
            await self._handle_session_event(content.get("data", {}))
        elif msg_type == "session.line":
            await self._handle_session_line(content.get("data", {}))
        elif msg_type == "session.pty_start":
            await self._handle_pty_start(content.get("data", {}))
        elif msg_type == "session.pty_output":
            await self._handle_pty_output(content.get("data", {}))
        elif msg_type == "session.pty_end":
            await self._handle_pty_end(content.get("data", {}))
        # Unknown message types are silently ignored.

    async def _handle_session_event(self, data: dict):
        session_id = data.get("session_id", "")
        jsonl_path = data.get("jsonl_path", "")
        provider = data.get("provider", "")
        role = data.get("role", "")
        text = data.get("text", "")

        if not session_id:
            return

        thread = await database_sync_to_async(get_or_create_observed_thread)(
            session_id, jsonl_path, provider
        )

        # Stamp the host server-side — never trust any host_id in the payload.
        if thread.host_id != self.host.id:
            thread.host = self.host
            await database_sync_to_async(thread.save)(update_fields=["host"])

        if role and text:
            await record_turn(thread, role, text)
            await self._deliver_to_telegram(
                thread, {"role": role, "text": text, "session_id": session_id}
            )

    async def _handle_session_line(self, data: dict):
        """Persist a single raw transcript line, parsed server-side.

        The daemon stays dumb and ships {provider, jsonl_path, raw}; the backend's
        own per-runtime parsers (the single source of truth) turn it into a turn.
        Session id is taken from the line, else a per-file remembered header id,
        else the file stem — so Codex/Gemini turns (no per-line id) attach to one
        thread per file. The host is always stamped server-side.
        """
        provider = data.get("provider", "")
        jsonl_path = data.get("jsonl_path", "")
        raw = data.get("raw", "")
        if not provider or not raw:
            return
        try:
            adapter = get_runtime_adapter(provider)
        except UnknownRuntimeError:
            return

        meta = adapter.extract_session_meta(raw)
        meta_session = meta.pop("session_id", None)
        if meta_session:
            self._file_sessions[jsonl_path] = meta_session

        parsed = adapter.parse_turn(raw)
        session_ref = (
            (parsed.get("session_id") if parsed else None)
            or self._file_sessions.get(jsonl_path)
            or (Path(jsonl_path).stem if jsonl_path else None)
        )
        if not session_ref:
            return

        thread = await database_sync_to_async(get_or_create_observed_thread)(
            session_ref, jsonl_path, provider
        )
        if thread.host_id != self.host.id:
            thread.host = self.host
            await database_sync_to_async(thread.save)(update_fields=["host"])
        if meta:
            await database_sync_to_async(apply_session_meta)(thread, meta)
        if parsed:
            await record_turn(thread, parsed["role"], parsed["text"])
            await self._deliver_to_telegram(thread, parsed)

    # ------------------------------------------------------------------
    # PTY frame handlers
    # ------------------------------------------------------------------

    async def _handle_pty_start(self, data: dict):
        session_name = data.get("session_name", "")
        command = data.get("command", "")
        cwd = data.get("cwd", "")
        if not session_name:
            return
        thread = await database_sync_to_async(self._get_or_create_pty_thread)(
            session_name, command, cwd
        )
        self._pty_threads[session_name] = str(thread.id)

    async def _handle_pty_output(self, data: dict):
        session_name = data.get("session_name", "")
        text = data.get("text", "")
        if not session_name or not text:
            return
        thread_id = self._pty_threads.get(session_name)
        if thread_id is None:
            return
        thread = await database_sync_to_async(Thread.objects.get)(id=thread_id)
        parsed = {"role": "assistant", "text": text, "session_id": session_name}
        await record_turn(thread, "assistant", text)
        await self._deliver_to_telegram(thread, parsed)

    async def _handle_pty_end(self, data: dict):
        session_name = data.get("session_name", "")
        if not session_name:
            return
        thread_id = self._pty_threads.get(session_name)
        if thread_id is None:
            return
        await database_sync_to_async(Thread.objects.filter(id=thread_id).update)(
            status=Thread.StatusChoices.COMPLETED
        )
        self._pty_threads.pop(session_name, None)

    def _get_or_create_pty_thread(self, session_name: str, command: str, cwd: str):
        """Synchronous helper — must be called via database_sync_to_async."""
        from apps.accounts.models import Account  # noqa: PLC0415

        account, _ = Account.objects.get_or_create(
            provider="pty",
            label="orc-run",
            defaults={"auth_type": "none", "credential_type": "none"},
        )
        existing = Thread.objects.filter(external_session_ref=session_name).first()
        if existing is not None:
            # Stamp host if not already set
            if existing.host_id != self.host.id:
                existing.host = self.host
                existing.save(update_fields=["host"])
            return existing
        return Thread.objects.create(
            external_session_ref=session_name,
            name=f"orc-run: {command[:80]}",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            host=self.host,
            account=account,
            status=Thread.StatusChoices.RUNNING,
            started_at=timezone.now(),
            metadata={
                "tmux_session_name": session_name,
                "command": command,
                "cwd": cwd,
            },
        )

    async def _deliver_to_telegram(self, thread, parsed):
        """Forward an observed turn from a remote host to the Telegram forum.

        Mirrors the local run_session_observer delivery path so multi-host
        sessions surface in the same inbox. Uses TELEGRAM_FORUM_CHAT_ID (the
        same setting the observer uses) so both paths route to one forum.
        Best-effort: delivery failures must never break transcript ingestion.

        Note: do NOT run run_session_observer on the same machine that connects
        a host daemon for the same sessions — both paths would deliver every
        turn, posting each twice.
        """
        forum_chat_id = getattr(settings, "TELEGRAM_FORUM_CHAT_ID", "")
        if not forum_chat_id:
            return
        try:
            await deliver_turn(
                thread, parsed, None, forum_chat_id=int(forum_chat_id)
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break ingestion
            logger.warning(
                "hostlink: telegram delivery failed: %s", redact_token(str(exc))
            )

    # Group handler for future downstream commands sent via channel_layer.group_send.
    async def host_command(self, event):
        await self.send_json(event)

    @database_sync_to_async
    def _get_host(self, host_id: str):
        try:
            return Host.objects.get(id=host_id)
        except (Host.DoesNotExist, Exception):
            return None
