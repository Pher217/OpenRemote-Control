from django.urls import path

from apps.hostlink.consumers import HostDaemonConsumer
from apps.threads.consumers import ThreadConsumer

websocket_urlpatterns = [
    path("ws/threads/<uuid:thread_id>/", ThreadConsumer.as_asgi()),
    path("ws/hosts/<uuid:host_id>/", HostDaemonConsumer.as_asgi()),
]
