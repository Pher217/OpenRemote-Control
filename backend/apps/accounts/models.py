import uuid

from django.db import models


class Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=64)
    label = models.CharField(max_length=255)
    auth_type = models.CharField(max_length=32)
    credential_type = models.CharField(max_length=32)
    encrypted_credential = models.BinaryField()
    credential_key_id = models.CharField(max_length=255)
    credential_recipient = models.CharField(max_length=255)
    credential_scheme_version = models.PositiveIntegerField(default=1)
    credential_rotated_at = models.DateTimeField(null=True, blank=True)
    credential_revoked_at = models.DateTimeField(null=True, blank=True)
    host_binding = models.ForeignKey(
        "hosts.Host",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bound_accounts",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider"]),
            models.Index(fields=["credential_key_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.label}"
