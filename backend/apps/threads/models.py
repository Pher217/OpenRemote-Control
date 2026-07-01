"""Thread primitive models: a single coding session/conversation.

Defines `Thread` (runtime, runtime mode, status lifecycle, host/account/project
links) and `Message` (roles, redacted and encrypted content, sequence order).
"""
import uuid

from django.db import models


class Thread(models.Model):
    class RuntimeModeChoices(models.TextChoices):
        PTY = "pty", "PTY"
        RC = "rc", "Remote Control"
        EXEC = "exec", "Exec"
        API = "api", "API"
        SDK = "sdk", "Claude Agent (SDK)"
        OBSERVED = "observed", "Observed"
        OPENCLAW = "openclaw", "OpenClaw"
        HERMES = "hermes", "Hermes"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        STARTING = "starting", "Starting"
        RUNNING = "running", "Running"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        STOPPED = "stopped", "Stopped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    runtime = models.CharField(max_length=64)
    runtime_mode = models.CharField(
        max_length=16, choices=RuntimeModeChoices.choices, default=RuntimeModeChoices.PTY
    )
    host = models.ForeignKey(
        "hosts.Host",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="threads",
    )
    account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        related_name="threads",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="threads",
    )
    status = models.CharField(
        max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING
    )
    external_session_ref = models.CharField(max_length=1024, blank=True)
    worktree_path = models.CharField(max_length=1024, blank=True)
    branch_name = models.CharField(max_length=255, blank=True)
    observed_jsonl_path = models.CharField(max_length=1024, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_event_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["runtime"]),
            models.Index(fields=["host"]),
            models.Index(fields=["external_session_ref"]),
        ]

    def __str__(self) -> str:
        return self.name


class Message(models.Model):
    class RoleChoices(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"
        TOOL = "tool", "Tool"
        SLASH = "slash", "Slash"
        SYSTEM_EVENT = "system_event", "System Event"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=RoleChoices.choices)
    redacted_content = models.TextField()
    raw_content_encrypted = models.BinaryField(null=True, blank=True)
    raw_retention_expires_at = models.DateTimeField(null=True, blank=True)
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    source_event_key = models.CharField(max_length=64, null=True, default=None, blank=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [
            models.Index(fields=["thread", "sequence"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "source_event_key"],
                condition=models.Q(source_event_key__isnull=False),
                name="unique_thread_source_event_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.thread.name} #{self.sequence} ({self.role})"
