"""Channels WebSocket consumer for thread events.

Handles per-thread connections, dispatches incoming text to the thread runtime,
and broadcasts events to all clients in the thread's channel group.
"""

import contextlib

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.threads.dispatch import dispatch_text
from apps.threads.models import Thread


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

        async def on_event(data):
            await self._emit(data)

        await dispatch_text(self.thread, text, on_event=on_event)

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
