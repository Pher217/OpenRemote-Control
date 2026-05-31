import uuid

from django.db import models


class AdapterCapability(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64)
    version = models.CharField(max_length=32)
    host = models.ForeignKey(
        "hosts.Host",
        on_delete=models.CASCADE,
        related_name="adapter_capabilities",
    )
    config = models.JSONField(default=dict, blank=True)
    is_available = models.BooleanField(default=False)
    last_probed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["name", "host"]]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}@{self.host.slug}"
