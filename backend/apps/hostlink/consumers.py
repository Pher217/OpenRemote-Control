"""WebSocket consumer for an enrolled host daemon.

Authenticates the daemon via per-host token and HMAC-signed nonce, then relays
backend commands and PTY streams while ingesting observed session events and
lines back into the local thread/telegram pipeline.
"""
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
        elif msg_type == "session.pty_reconcile":
            await self._handle_pty_reconcile(content.get("data", {}))
        elif msg_type == "session.headless_start":
            await self._handle_headless_start(content.get("data", {}))
        elif msg_type == "session.headless_reply":
            await self._handle_headless_reply(content.get("data", {}))
        elif msg_type == "host_heartbeat":
            # Echo a ping back THROUGH the group path (group_send → Redis →
            # this consumer's host_command → ws). This exercises the exact
            # delivery path used by pty.inject, so the daemon's watchdog can
            # detect a silently-stalled channel receive and force a reconnect.
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "host_command", "command": "ping", "nonce": content.get("nonce", "")},
            )
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
        if thread.host_id != self.host.id or not (thread.metadata or {}).get("host_name"):
            thread.host = self.host
            thread.metadata = {**(thread.metadata or {}), "host_name": self.host.name}
            await database_sync_to_async(thread.save)(update_fields=["host", "metadata"])

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
        if thread.host_id != self.host.id or not (thread.metadata or {}).get("host_name"):
            thread.host = self.host
            thread.metadata = {**(thread.metadata or {}), "host_name": self.host.name}
            await database_sync_to_async(thread.save)(update_fields=["host", "metadata"])
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
        claude_session_id = data.get("claude_session_id", "")
        if not session_name:
            return
        thread = await database_sync_to_async(self._get_or_create_pty_thread)(
            session_name, command, cwd, claude_session_id
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
        # Raw PTY frames are terminal screen redraws (box-drawing, spinners, status
        # lines) for a TUI app like Claude — NOT clean conversation turns. Record
        # them as debug telemetry only (source="pty_screen") and do NOT deliver to
        # Telegram. Clean output reaches the topic exclusively via the parsed JSONL
        # transcript (session.line / session.event), which resolves to this same
        # thread by external_session_ref. See drive-unify spec PR 2.
        await record_turn(thread, "assistant", text, source="pty_screen")

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

    async def _handle_pty_reconcile(self, data: dict):
        """Mark RUNNING PTY threads for this host COMPLETED when their tmux session is gone.

        Fail-safe: if session_names key is absent the frame is treated as a no-op.
        Scope is strictly limited to self.host — never touches other hosts' threads.
        """
        if "session_names" not in data:
            return
        live = set(data["session_names"])
        host_id = self.host.id

        def _reconcile():
            # Decide in Python to avoid JSONField exclude/NULL pitfalls (a missing
            # key is NOT "false"). Only tmux-backed PTY sessions are reconciled:
            #   - a thread is completed iff it has a real tmux_session_name that is
            #     no longer in the live set.
            #   - headless threads (tmux_session_name is None) and threads with no
            #     tmux name are skipped — their liveness is not tmux-based.
            candidates = Thread.objects.filter(
                host_id=host_id,
                runtime_mode=Thread.RuntimeModeChoices.PTY,
                status=Thread.StatusChoices.RUNNING,
            )
            stale_ids = [
                t.id
                for t in candidates
                if (name := (t.metadata or {}).get("tmux_session_name"))
                and name not in live
                and not (t.metadata or {}).get("headless")
            ]
            if stale_ids:
                Thread.objects.filter(id__in=stale_ids).update(
                    status=Thread.StatusChoices.COMPLETED
                )

        await database_sync_to_async(_reconcile)()

    # ------------------------------------------------------------------
    # Headless Claude frame handlers
    # ------------------------------------------------------------------

    async def _handle_headless_start(self, data: dict):
        session_name = data.get("session_name", "")
        claude_session_id = data.get("claude_session_id", "")
        cwd = data.get("cwd", "")
        if not session_name or not claude_session_id:
            return

        def _get_or_create():
            from apps.accounts.models import Account  # noqa: PLC0415

            account, _ = Account.objects.get_or_create(
                provider="pty",
                label="orc-run",
                defaults={"auth_type": "none", "credential_type": "none"},
            )
            thread, created = Thread.objects.get_or_create(
                external_session_ref=session_name,
                defaults={
                    "name": f"headless: {session_name}",
                    "runtime": "pty",
                    "runtime_mode": Thread.RuntimeModeChoices.PTY,
                    "host": self.host,
                    "account": account,
                    "status": Thread.StatusChoices.RUNNING,
                    "started_at": timezone.now(),
                    "metadata": {
                        "headless": True,
                        "claude_session_id": claude_session_id,
                        "cwd": cwd,
                        "tmux_session_name": None,
                        "host_name": self.host.name,
                    },
                },
            )
            if not created:
                md = dict(thread.metadata or {})
                md["headless"] = True
                md["claude_session_id"] = claude_session_id
                md["cwd"] = cwd
                md.setdefault("tmux_session_name", None)
                md["host_name"] = self.host.name
                thread.metadata = md
                if thread.host_id != self.host.id:
                    thread.host = self.host
                thread.save(update_fields=["metadata", "host"])
            return thread

        thread = await database_sync_to_async(_get_or_create)()
        await self._deliver_to_telegram(
            thread,
            {
                "role": "assistant",
                "text": "🤖 Headless Claude session ready — reply in this topic to send a prompt.",
                "session_id": session_name,
            },
        )

    async def _handle_headless_reply(self, data: dict):
        thread_id = data.get("thread_id", "")
        text = data.get("text", "")
        if not thread_id or not text:
            return

        try:
            thread = await database_sync_to_async(Thread.objects.get)(id=thread_id)
        except Thread.DoesNotExist:
            return

        await record_turn(thread, "assistant", text)
        await self._deliver_to_telegram(
            thread, {"role": "assistant", "text": text, "session_id": str(thread.id)}
        )

        def _mark_started():
            md = dict(thread.metadata or {})
            md["claude_session_started"] = True
            Thread.objects.filter(id=thread.id).update(metadata=md)

        await database_sync_to_async(_mark_started)()

    def _get_or_create_pty_thread(
        self, session_name: str, command: str, cwd: str, claude_session_id: str = ""
    ):
        """Synchronous helper — must be called via database_sync_to_async.

        Keyed by the Claude session id when present (``--session-id`` UUID), so the
        PTY thread and the JSONL-transcript observation resolve to ONE canonical
        thread — clean output (parsed turns) and input (``tmux send-keys``) share a
        single Telegram topic. Falls back to the tmux session name for non-claude
        commands. An existing thread (e.g. created first by transcript observation)
        is upgraded to driveable PTY with its tmux session name attached.
        """
        from apps.accounts.models import Account  # noqa: PLC0415

        account, _ = Account.objects.get_or_create(
            provider="pty",
            label="orc-run",
            defaults={"auth_type": "none", "credential_type": "none"},
        )
        ref = claude_session_id or session_name
        existing = Thread.objects.filter(external_session_ref=ref).first()
        if existing is not None:
            md = {**(existing.metadata or {})}
            if existing.host_id != self.host.id or not md.get("host_name"):
                existing.host = self.host
                md["host_name"] = self.host.name
            md["tmux_session_name"] = session_name
            if claude_session_id:
                md["claude_session_id"] = claude_session_id
            existing.metadata = md
            existing.runtime_mode = Thread.RuntimeModeChoices.PTY
            existing.save(update_fields=["host", "metadata", "runtime_mode"])
            return existing
        metadata = {
            "tmux_session_name": session_name,
            "command": command,
            "cwd": cwd,
            "host_name": self.host.name,
        }
        if claude_session_id:
            metadata["claude_session_id"] = claude_session_id
        return Thread.objects.create(
            external_session_ref=ref,
            name=f"orc-run: {command[:80]}",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            host=self.host,
            account=account,
            status=Thread.StatusChoices.RUNNING,
            started_at=timezone.now(),
            metadata=metadata,
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
