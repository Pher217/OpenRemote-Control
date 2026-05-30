from django.db import models


class TelegramChat(models.Model):
    chat_id = models.BigIntegerField(unique=True, db_index=True)
    thread = models.OneToOneField(
        "threads.Thread",
        on_delete=models.CASCADE,
        related_name="telegram_chat",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"telegram:{self.chat_id}"
