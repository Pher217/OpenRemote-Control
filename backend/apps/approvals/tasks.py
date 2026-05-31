from celery import shared_task
from django.utils import timezone

from apps.approvals.models import ApprovalRequest


@shared_task
def expire_old_approval_requests():
    expired = ApprovalRequest.objects.filter(
        status=ApprovalRequest.StatusChoices.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=ApprovalRequest.StatusChoices.EXPIRED)
    return expired
