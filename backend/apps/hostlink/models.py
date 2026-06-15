"""Enrollment token models for the multi-host link.

HostToken stores one-way SHA-256 hashes of short-lived tokens used to
authenticate a host during enrollment and rotation.
"""
import hashlib
import hmac
import secrets

from django.db import models
from django.utils import timezone


class HostToken(models.Model):
    host = models.ForeignKey(
        "hosts.Host",
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    token_hash = models.CharField(max_length=64)  # sha256 hex of the raw token
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"HostToken({self.host_id}, active={self.revoked_at is None})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def issue(cls, host) -> tuple["HostToken", str]:
        """Generate a new token for *host*. Returns (HostToken, raw_token).

        The raw token is returned exactly once. Only the sha256 hex is stored.
        Revokes all existing active tokens for the host before issuing a new one.
        """
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        now = timezone.now()
        cls.objects.filter(host=host, revoked_at__isnull=True).update(
            revoked_at=now, rotated_at=now
        )
        token = cls.objects.create(host=host, token_hash=token_hash)
        return token, raw

    @classmethod
    def verify(cls, host, raw_token: str) -> bool:
        """Return True if *raw_token* matches an active token for *host*.

        Uses constant-time comparison to prevent timing attacks.
        """
        candidate_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token = cls.objects.filter(host=host, revoked_at__isnull=True).first()
        if token is None:
            return False
        return hmac.compare_digest(candidate_hash, token.token_hash)
