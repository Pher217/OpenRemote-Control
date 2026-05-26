
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class ThreadConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.thread_id = self.scope["url_route"]["kwargs"]["thread_id"]
        self.room_group_name = f"thread_{self.thread_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive_json(self, content):
        await self.send_json(content)

    async def thread_update(self, event):
        await self.send_json(event["data"])
