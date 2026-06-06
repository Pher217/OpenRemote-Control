from django.db import models


class ConnectorInstance(models.Model):
    """Records which connector made each call (identity binding v1).
    Full per-connector keypair authentication is a UC0 item — for now the
    shared ORC_CONNECTOR_TOKEN is the only gate; this model provides the
    audit trail and thread binding.
    """

    connector_id = models.CharField(max_length=255, unique=True, db_index=True)
    tool = models.CharField(max_length=64)
    workspace_root = models.CharField(max_length=1024, blank=True)
    thread = models.ForeignKey(
        "threads.Thread",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connector_instances",
    )
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at"]

    def __str__(self) -> str:
        return f"{self.connector_id} ({self.tool})"
