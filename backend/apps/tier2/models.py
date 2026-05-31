import uuid

from django.db import models


class Tier2Provider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64)
    base_url = models.URLField()
    api_version = models.CharField(max_length=32, blank=True)
    is_available = models.BooleanField(default=False)
    last_probed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
