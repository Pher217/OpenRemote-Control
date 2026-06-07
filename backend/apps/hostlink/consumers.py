import time
from pathlib import Path

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache

from apps.hostlink import security
from apps.hostlink.models import HostToken
from apps.hosts.models import Host
from apps.observe.runtimes import UnknownRuntimeError, get_runtime_adapter
from apps.observe.service import (
    apply_session_meta,
    get_or_create_observed_thread,
    record_turn,
)


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

    # Group handler for future downstream commands sent via channel_layer.group_send.
    async def host_command(self, event):
        await self.send_json(event)

    @database_sync_to_async
    def _get_host(self, host_id: str):
        try:
            return Host.objects.get(id=host_id)
        except (Host.DoesNotExist, Exception):
            return None
