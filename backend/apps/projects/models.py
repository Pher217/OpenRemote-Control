"""Project model grouping repository context, sensitivity, policy profile,
and allowed accounts, hosts and runtimes for sessions.
"""

import uuid

from django.db import models


class Project(models.Model):
    class SensitivityChoices(models.TextChoices):
        PUBLIC = "public", "Public"
        INTERNAL = "internal", "Internal"
        CONFIDENTIAL = "confidential", "Confidential"
        REGULATED = "regulated", "Regulated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=255)
    repo_url = models.URLField(blank=True)
    sensitivity = models.CharField(
        max_length=16, choices=SensitivityChoices.choices, default=SensitivityChoices.INTERNAL
    )
    policy = models.ForeignKey(
        "policies.PolicyProfile",
        on_delete=models.PROTECT,
        related_name="projects",
    )
    local_paths = models.JSONField(default=dict, blank=True)
    allowed_accounts = models.ManyToManyField(
        "accounts.Account",
        blank=True,
        related_name="allowed_projects",
    )
    allowed_hosts = models.ManyToManyField(
        "hosts.Host",
        blank=True,
        related_name="allowed_projects",
    )
    allowed_runtimes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name
