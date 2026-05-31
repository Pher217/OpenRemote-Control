from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.audit.models import AuditEvent


@shared_task
def cleanup_old_audit_events(retention_days=90):
    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted, _ = AuditEvent.objects.filter(timestamp__lt=cutoff).delete()
    return deleted
