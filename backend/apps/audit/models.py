"""Audit log models.

Defines AuditEvent, an append-only record of system activity with
redacted and encrypted payload retention fields.
"""

from django.db import models


class AuditEvent(models.Model):
    class EventTypeChoices(models.TextChoices):
        THREAD_CREATE = "thread_create", "Thread Create"
        MESSAGE_SEND = "message_send", "Message Send"
        APPROVAL_REQUEST = "approval_request", "Approval Request"
        APPROVAL_GRANT = "approval_grant", "Approval Grant"
        POLICY_BLOCK = "policy_block", "Policy Block"
        COMMAND_CLASSIFY = "command_classify", "Command Classify"
        RUNTIME_START = "runtime_start", "Runtime Start"
        RUNTIME_STOP = "runtime_stop", "Runtime Stop"
        REDACTION = "redaction", "Redaction"

    id = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    thread = models.ForeignKey(
        "threads.Thread",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.CharField(max_length=255)
    event_type = models.CharField(max_length=32, choices=EventTypeChoices.choices)
    redacted_payload = models.JSONField(default=dict)
    raw_payload_encrypted = models.BinaryField(null=True, blank=True)
    raw_retention_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["event_type"]),
            models.Index(fields=["actor"]),
            models.Index(fields=["timestamp", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} by {self.actor} at {self.timestamp}"
