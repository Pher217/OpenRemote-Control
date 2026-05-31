from django.urls import path

from apps.threads.consumers import ThreadConsumer

websocket_urlpatterns = [
    path("ws/threads/<uuid:thread_id>/", ThreadConsumer.as_asgi()),
]
