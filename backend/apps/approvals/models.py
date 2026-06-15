"""Approval request models.

Defines ApprovalRequest, a gated action awaiting operator approval with
risk classification, status tracking and signed nonce support.
"""
import uuid

from django.db import models


class ApprovalRequest(models.Model):
    class RequestTypeChoices(models.TextChoices):
        RUN_COMMAND = "run_command", "Run Command"
        PUSH_BRANCH = "push_branch", "Push Branch"
        OPEN_PR = "open_pr", "Open PR"
        INSTALL_PACKAGE = "install_package", "Install Package"
        NETWORK = "network", "Network"
        DEPLOY = "deploy", "Deploy"
        CROSS_ACCOUNT_FORK = "cross_account_fork", "Cross-Account Fork"

    class RiskChoices(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        DESTRUCTIVE = "destructive", "Destructive"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        "threads.Thread",
        on_delete=models.CASCADE,
        related_name="approval_requests",
    )
    request_type = models.CharField(max_length=32, choices=RequestTypeChoices.choices)
    risk = models.CharField(max_length=16, choices=RiskChoices.choices)
    summary = models.CharField(max_length=255)
    preview = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=StatusChoices.choices, default=StatusChoices.PENDING
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField()
    signed_nonce = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["risk"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.request_type} ({self.status})"
