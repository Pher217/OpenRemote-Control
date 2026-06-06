import uuid

from django.db import models


class Prompt(models.Model):
    class PromptType(models.TextChoices):
        NOTICE = "notice", "Notice"
        CHOICE_SINGLE = "choice_single", "Single Choice"
        CHOICE_MULTI = "choice_multi", "Multi Choice"
        FREE_TEXT = "free_text", "Free Text"
        APPROVAL = "approval", "Approval"

    class TrustClass(models.TextChoices):
        INFORMATIONAL = "informational", "Informational"
        DECISION = "decision", "Decision"
        APPROVAL = "approval", "Approval"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        ANSWERED = "answered", "Answered"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(
        "threads.Thread",
        on_delete=models.CASCADE,
        related_name="prompts",
    )
    prompt_type = models.CharField(max_length=16, choices=PromptType.choices)
    trust_class = models.CharField(
        max_length=16,
        choices=TrustClass.choices,
        default=TrustClass.DECISION,
    )
    question = models.CharField(max_length=500)
    body = models.TextField(blank=True, default="")
    options = models.JSONField(default=list)
    min_choices = models.PositiveIntegerField(default=0)
    max_choices = models.PositiveIntegerField(default=1)
    nonce = models.CharField(max_length=64, db_index=True)
    prompt_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=StatusChoices.choices,
        default=StatusChoices.PENDING,
    )
    surface_message_ref = models.JSONField(default=dict)
    response = models.JSONField(null=True, blank=True)
    answered_by = models.CharField(max_length=255, blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"{self.prompt_type} ({self.status}): {self.question[:60]}"

    def is_expired(self, now) -> bool:
        return now >= self.expires_at

    def record_response(self, option_keys=None, text=None, by="") -> None:
        from django.utils import timezone

        if option_keys is not None:
            self.response = {"option_keys": option_keys}
        elif text is not None:
            self.response = {"text": text}
        self.status = self.StatusChoices.ANSWERED
        self.answered_at = timezone.now()
        self.answered_by = by
