import contextlib

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.slash.handlers import get_handler
from apps.slash.parser import parse
from apps.threads.models import Message, Thread


class ThreadConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.thread_id = self.scope["url_route"]["kwargs"]["thread_id"]
        self.thread = await self._get_thread(self.thread_id)
        if self.thread is None:
            await self.close()
            return
        self.room_group_name = f"thread_{self.thread_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if getattr(self, "room_group_name", None):
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )

    async def receive_json(self, content):
        text = (content or {}).get("text", "")
        if not text.strip():
            await self.send_json({"type": "error", "message": "empty message"})
            return

        parsed = parse(text)

        if parsed[0] == "slash":
            cmd, args = parsed[1], parsed[2]
            await self._persist_message(role="slash", text=text)
            handler = get_handler(cmd)
            if handler is None:
                await self.send_json(
                    {
                        "type": "slash_result",
                        "ok": False,
                        "message": f"Unknown command: /{cmd}",
                    }
                )
                return
            result = await database_sync_to_async(handler)(self.thread, args)
            self.thread = await self._get_thread(self.thread_id)
            await self.send_json({"type": "slash_result", **result})
            return

        await self._persist_message(role="user", text=text)
        history = await self._build_history()

        from apps.tier2.base import UnknownProviderError, get_adapter

        try:
            adapter = get_adapter(self.thread.account.provider)
        except UnknownProviderError:
            await self.send_json(
                {
                    "type": "error",
                    "message": f"No adapter for provider {self.thread.account.provider}",
                }
            )
            return

        full = ""
        async for ev in adapter.stream(self.thread, history):
            if ev.kind == "message_delta":
                chunk = ev.payload.get("text", "")
                full += chunk
                await self._emit({"type": "message_delta", "text": chunk})
            elif ev.kind == "message_complete":
                full = ev.payload.get("text") or full
                msg = await self._persist_message(role="assistant", text=full)
                await self._emit(
                    {
                        "type": "message_complete",
                        "text": full,
                        "sequence": msg.sequence,
                        "message_id": str(msg.id),
                    }
                )
            elif ev.kind == "error":
                await self._emit(
                    {"type": "error", "message": ev.payload.get("message", "")}
                )
                break

    async def _emit(self, data):
        await self.send_json(data)
        with contextlib.suppress(Exception):
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "thread.message", "data": data, "sender": self.channel_name},
            )

    async def thread_message(self, event):
        if event.get("sender") == self.channel_name:
            return
        await self.send_json(event["data"])

    async def thread_update(self, event):
        await self.send_json(event["data"])

    @database_sync_to_async
    def _get_thread(self, thread_id):
        try:
            return Thread.objects.select_related("account").get(id=thread_id)
        except Thread.DoesNotExist:
            return None

    @database_sync_to_async
    def _persist_message(self, role, text):
        from django.db.models import Max

        nxt = (
            Message.objects.filter(thread=self.thread).aggregate(m=Max("sequence"))["m"]
            or 0
        ) + 1
        return Message.objects.create(
            thread=self.thread, role=role, redacted_content=text, sequence=nxt
        )

    @database_sync_to_async
    def _build_history(self):
        allowed = {"user", "assistant", "system"}
        return [
            {"role": m.role, "content": m.redacted_content}
            for m in Message.objects.filter(thread=self.thread).order_by("sequence")
            if m.role in allowed
        ]
