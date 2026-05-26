from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.approvals.models import ApprovalRequest
from apps.threads.models import Message, Thread

from .models import AuditEvent


def _create_audit_event(thread, event_type, actor="system", payload=None):
    AuditEvent.objects.create(
        thread=thread,
        event_type=event_type,
        actor=actor,
        redacted_payload=payload or {},
    )


@receiver(post_save, sender=Thread)
def thread_post_save(sender, instance, created, **kwargs):
    if created:
        _create_audit_event(
            instance,
            AuditEvent.EventTypeChoices.THREAD_CREATE,
            payload={"name": instance.name, "runtime": instance.runtime, "mode": instance.runtime_mode},
        )


@receiver(post_save, sender=Message)
def message_post_save(sender, instance, created, **kwargs):
    if created:
        _create_audit_event(
            instance.thread,
            AuditEvent.EventTypeChoices.MESSAGE_SEND,
            payload={"role": instance.role, "sequence": instance.sequence},
        )


@receiver(post_save, sender=ApprovalRequest)
def approval_request_post_save(sender, instance, created, **kwargs):
    if created:
        _create_audit_event(
            instance.thread,
            AuditEvent.EventTypeChoices.APPROVAL_REQUEST,
            payload={"request_type": instance.request_type, "risk": instance.risk, "summary": instance.summary},
        )
    elif instance.status in (ApprovalRequest.StatusChoices.APPROVED, ApprovalRequest.StatusChoices.REJECTED):
        _create_audit_event(
            instance.thread,
            AuditEvent.EventTypeChoices.APPROVAL_GRANT,
            payload={"status": instance.status, "decided_by": instance.decided_by},
        )
