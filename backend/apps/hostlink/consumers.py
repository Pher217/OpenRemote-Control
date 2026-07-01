"""WebSocket consumer for an enrolled host daemon.

Authenticates the daemon via per-host token and HMAC-signed nonce, then relays
backend commands and drives PTY / headless Claude sessions, streaming their
turns back into the local thread/telegram pipeline.
"""
import asyncio
import logging
import time
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.hostlink import security
from apps.hostlink.models import HostToken
from apps.hosts.models import Host
from apps.observe.delivery import deliver_turn
from apps.observe.service import record_turn
from apps.telegram.telegram_api import redact_token
from apps.threads.models import Thread

logger = logging.getLogger(__name__)

# Telegram delivery is offloaded to a per-connection background drainer so a slow
# or rate-limited (429) send never blocks the receive loop — blocking it starves
# the host-heartbeat echo and drops the daemon ws (the "streaming stalls then
# reconnects every 90s" bug). The drainer throttles sends to stay under Telegram's
# per-chat rate limit; the queue is bounded and drops oldest under sustained burst
# (turns are already persisted via record_turn — the daemon re-sends on reconnect).
_DELIVERY_QUEUE_MAX = 2000
_DELIVERY_MIN_INTERVAL = 0.5  # seconds between Telegram sends per connection
_DRAINER_STOP = object()  # sentinel: tells the drainer to drain + exit on disconnect


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
        # Per-PTY-session thread id cache: session_name -> str(thread.id)
        self._pty_threads: dict[str, str] = {}
        # Background Telegram delivery queue + drainer (see module note).
        self._delivery_queue: asyncio.Queue = asyncio.Queue(maxsize=_DELIVERY_QUEUE_MAX)
        self._closing = False
        self._delivery_task = asyncio.create_task(self._delivery_drainer())
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._resync_tail_sessions()

    async def disconnect(self, close_code):
        # Drain pending deliveries before tearing down so already-recorded
        # turns/intros aren't lost when a short-lived control ws (`orc-host
        # headless` registration) or a churning daemon ws closes mid-stream
        # (the "input worked but no reply showed up" bug).
        #
        # Cooperative shutdown, NOT cancel-then-flush: cancelling could abort an
        # in-flight deliver_turn whose item was already dequeued (lost turn).
        # Instead signal close (the drainer skips its throttle) and push a STOP
        # sentinel; the drainer finishes the in-flight send, drains the rest fast,
        # and exits. We await it (bounded), only cancelling as a last resort.
        self._closing = True
        queue = getattr(self, "_delivery_queue", None)
        task = getattr(self, "_delivery_task", None)
        if queue is not None and task is not None:
            try:
                queue.put_nowait(_DRAINER_STOP)
            except asyncio.QueueFull:
                try:  # make room for the sentinel
                    queue.get_nowait()
                    queue.task_done()
                    queue.put_nowait(_DRAINER_STOP)
                except Exception:  # noqa: BLE001
                    task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()
        elif task is not None:
            task.cancel()
        if getattr(self, "group_name", None):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        if not isinstance(content, dict):
            return
        msg_type = content.get("type")
        if msg_type == "session.pty_start":
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
        elif msg_type == "session.turn":
            await self._handle_session_turn(content.get("data", {}))
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
        # Telegram. Clean turns for a driveable session arrive via the headless
        # reply path (session.headless_reply → _deliver_to_telegram). A raw `orc
        # run` TUI session has no clean-output stream — drive via /openremote-control
        # (headless) for write+stream.
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
        # reset=True (first step of a streamed turn) is delivered IN-ORDER through
        # the delivery queue so a fast next turn can't clear the digest before the
        # previous turn's still-queued chunks have drained. deliver_turn starts a
        # fresh progress digest when it sees reset, giving one edited message/turn.
        await self._deliver_to_telegram(
            thread,
            {
                "role": "assistant",
                "text": text,
                "session_id": str(thread.id),
                "reset": bool(data.get("reset")),
            },
        )

        def _mark_started():
            md = dict(thread.metadata or {})
            md["claude_session_started"] = True
            Thread.objects.filter(id=thread.id).update(metadata=md)

        await database_sync_to_async(_mark_started)()

    async def _handle_session_turn(self, data: dict):
        """Persist + deliver a JSONL-tailed editor turn (session.turn frame).

        Idempotent at the DB layer via record_turn's source_event_key
        constraint — the daemon may re-send the same transcript event after a
        restart or ws reconnect, so a duplicate must be dropped, not re-delivered.
        """
        thread_id = data.get("thread_id", "")
        role = data.get("role", "")
        text = data.get("text", "")
        source_event_key = data.get("source_event_key", "")

        try:
            uuid.UUID(str(thread_id))
        except (ValueError, TypeError):
            logger.warning("hostlink: session.turn dropped — invalid thread_id")
            return
        if role not in {"user", "assistant"}:
            logger.warning("hostlink: session.turn dropped — invalid role %r", role)
            return
        if not text or not source_event_key:
            logger.warning("hostlink: session.turn dropped — missing text/source_event_key")
            return

        thread = await database_sync_to_async(
            Thread.objects.filter(id=thread_id, host_id=self.host.id).first
        )()
        if thread is None:
            logger.warning(
                "hostlink: session.turn dropped — thread %s not owned by host %s",
                thread_id, self.host.id,
            )
            return

        message = await record_turn(thread, role, text, source_event_key=source_event_key)
        if message is None:
            return

        delivered_text = f"🧑 {text}" if role == "user" else text
        await self._deliver_to_telegram(
            thread,
            {
                "role": role,
                "text": delivered_text,
                "session_id": str(thread.id),
                # Engages the existing TTL delivery-dedup as a throttle; the
                # DB unique constraint on source_event_key is the correctness layer.
                "uuid": source_event_key,
            },
        )

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
        """Enqueue a turn for throttled, non-blocking Telegram delivery.

        Offloaded to the background drainer (see module note) so a slow or
        rate-limited Telegram call never blocks the receive loop / heartbeat.
        Best-effort: under a sustained burst the oldest pending turn is dropped
        (it is already persisted via record_turn; the daemon re-sends on
        reconnect). Synchronous and fast — never awaits a network call.
        """
        if not getattr(settings, "TELEGRAM_FORUM_CHAT_ID", ""):
            return
        queue = getattr(self, "_delivery_queue", None)
        if queue is None:
            return
        try:
            queue.put_nowait((thread, parsed))
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop oldest, make room for the newest turn
                queue.task_done()
                queue.put_nowait((thread, parsed))
            except Exception:  # noqa: BLE001 — best-effort
                pass

    async def _delivery_drainer(self) -> None:
        """Serialize Telegram deliveries off the receive loop, throttled.

        One send at a time with a fixed inter-send delay keeps us under
        Telegram's per-chat rate limit; individual 429s are handled (with
        retry_after backoff) inside telegram_api.send_message. On disconnect a
        _DRAINER_STOP sentinel makes it drain the remainder (without throttle,
        since self._closing is set) and exit cleanly — so no in-flight turn is
        lost to task cancellation.
        """
        forum_chat_id_raw = getattr(settings, "TELEGRAM_FORUM_CHAT_ID", "")
        if not forum_chat_id_raw:
            return
        try:
            forum_chat_id = int(forum_chat_id_raw)
        except (TypeError, ValueError):
            return
        while True:
            item = await self._delivery_queue.get()
            if item is _DRAINER_STOP:
                self._delivery_queue.task_done()
                return
            thread, parsed = item
            try:
                await deliver_turn(thread, parsed, None, forum_chat_id=forum_chat_id)
            except Exception as exc:  # noqa: BLE001 — best-effort, never crash the drainer
                logger.warning(
                    "hostlink: telegram delivery failed: %s", redact_token(str(exc))
                )
            finally:
                self._delivery_queue.task_done()
            if not getattr(self, "_closing", False):
                await asyncio.sleep(_DELIVERY_MIN_INTERVAL)

    async def _resync_tail_sessions(self):
        """On (re)connect, tell the daemon which sessions to tail.

        The daemon may have restarted or reconnected mid-session, losing track
        of which threads it should be tailing JSONL transcripts for. Sent
        directly down THIS socket (send_json, not group_send) so it lands only
        on the daemon that just (re)connected. Capped — a runaway thread count
        for one host is logged rather than flooding the fresh connection.
        """
        resync_limit = 20
        # Any non-terminal thread keeps its tail armed — dispatched threads sit
        # at PENDING until first activity, and filtering on RUNNING alone
        # silently dropped their mirror after a daemon restart (found live
        # 2026-07-01, thread 4a6508fb).
        threads = await database_sync_to_async(list)(
            Thread.objects.filter(host_id=self.host.id)
            .exclude(
                status__in=[
                    Thread.StatusChoices.COMPLETED,
                    Thread.StatusChoices.FAILED,
                    Thread.StatusChoices.STOPPED,
                ]
            )
            .exclude(metadata__claude_session_id="")[:resync_limit + 1]
        )
        if len(threads) > resync_limit:
            logger.warning(
                "hostlink: host %s has more than %d running headless threads; "
                "resync capped", self.host.id, resync_limit,
            )
            threads = threads[:resync_limit]
        for thread in threads:
            md = thread.metadata or {}
            claude_session_id = md.get("claude_session_id")
            if not claude_session_id:
                continue
            await self.send_json(
                {
                    "type": "host_command",
                    "command": "tail.start",
                    "thread_id": str(thread.id),
                    "claude_session_id": claude_session_id,
                    "cwd": md.get("cwd", ""),
                    "provider": "claude",
                }
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
