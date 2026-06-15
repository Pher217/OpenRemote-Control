"""Django signal handlers for the threads app.

Broadcasts thread update events to the channel layer group
when a Thread instance is saved.
"""

import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.threads.models import Thread

logger = structlog.get_logger(__name__)


@receiver(post_save, sender=Thread)
def thread_post_save_broadcast(sender, instance, created, **kwargs):
    if created:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    group_name = f"thread_{instance.id}"
    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "thread_update",
                "data": {
                    "id": str(instance.id),
                    "status": instance.status,
                    "runtime": instance.runtime,
                    "runtime_mode": instance.runtime_mode,
                    "updated_at": instance.updated_at.isoformat() if instance.updated_at else None,
                },
            },
        )
    except Exception as exc:
        logger.warning("thread broadcast failed", error=str(exc))
        return
