from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.hosts.models import Host


@shared_task
def check_host_heartbeats(timeout_minutes=5):
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    offline = Host.objects.filter(
        status__in=(Host.StatusChoices.ONLINE, Host.StatusChoices.DEGRADED),
        last_seen_at__lt=cutoff,
    ).update(status=Host.StatusChoices.OFFLINE)
    return offline
