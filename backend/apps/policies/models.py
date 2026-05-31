import uuid

from django.db import models


class PolicyProfile(models.Model):
    class SensitivityChoices(models.TextChoices):
        PUBLIC = "public", "Public"
        INTERNAL = "internal", "Internal"
        CONFIDENTIAL = "confidential", "Confidential"
        REGULATED = "regulated", "Regulated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    sensitivity_max = models.CharField(
        max_length=16, choices=SensitivityChoices.choices, default=SensitivityChoices.INTERNAL
    )
    runtime_modes_allowed = models.JSONField(default=list, blank=True)
    providers_allowed = models.JSONField(default=list, blank=True)
    provider_jurisdictions_allowed = models.JSONField(default=list, blank=True)
    account_orgs_allowed = models.JSONField(default=list, blank=True)
    hosts_allowed = models.JSONField(default=list, blank=True)
    egress_allowed = models.BooleanField(default=False)
    rc_via_anthropic_allowed = models.BooleanField(default=False)
    cloud_models_allowed = models.BooleanField(default=False)
    data_classes_allowed = models.JSONField(default=list, blank=True)
    raw_retention_max_days = models.PositiveIntegerField(default=0)
    require_worktree = models.BooleanField(default=True)
    require_approval_for = models.JSONField(default=list, blank=True)
    block_destructive = models.BooleanField(default=True)
    max_runtime_minutes = models.PositiveIntegerField(default=60)
    max_parallel_threads = models.PositiveIntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
