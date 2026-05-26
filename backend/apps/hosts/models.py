import uuid

from django.db import models


class Host(models.Model):
    class OsChoices(models.TextChoices):
        DARWIN = "darwin", "Darwin"
        LINUX = "linux", "Linux"
        WIN32 = "win32", "Windows"

    class StatusChoices(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        DEGRADED = "degraded", "Degraded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=255)
    os = models.CharField(max_length=16, choices=OsChoices.choices)
    tailscale_dns = models.CharField(max_length=255, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=StatusChoices.choices, default=StatusChoices.OFFLINE
    )
    capabilities = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at"]

    def __str__(self) -> str:
        return self.name
