from django.db import models


class GatewayChat(models.Model):
    """Maps a (platform, chat_id) pair to a Thread for inbound routing."""

    platform = models.CharField(max_length=32)
    chat_id = models.CharField(max_length=255)
    thread = models.ForeignKey(
        "threads.Thread",
        on_delete=models.CASCADE,
        related_name="gateway_chats",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("platform", "chat_id")]

    def __str__(self) -> str:
        return f"gateway:{self.platform}:{self.chat_id}"


class GatewayMessage(models.Model):
    """Outbound message queued for delivery by the Node sidecar."""

    platform = models.CharField(max_length=32)
    recipient = models.CharField(max_length=255)
    text = models.TextField()
    prompt_nonce = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["platform", "delivered_at"]),
        ]

    def __str__(self) -> str:
        return f"gateway-msg:{self.platform}:{self.recipient} (delivered={self.delivered_at is not None})"
