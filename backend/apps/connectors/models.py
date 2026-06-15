"""Django models for the connectors app: the MCP bridge backend.

Stores connector identities, Ed25519 public keys, one-time pairing codes,
and connector instances used to route messages between coding-agent
sessions and chat surfaces.
"""

import secrets

from django.db import models
from django.utils import timezone


class ConnectorKey(models.Model):
    """Per-connector Ed25519 public key — the UC0 identity credential.

    One connector may have multiple keys (key rotation), but only active
    (non-revoked) keys are accepted for signature verification.
    """

    connector_id = models.CharField(max_length=255, unique=True, db_index=True)
    key_id = models.CharField(max_length=64)
    public_key = models.CharField(max_length=128)  # standard base64 ed25519 (44 chars)
    tool = models.CharField(max_length=64)
    label = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.connector_id}/{self.key_id} ({self.tool})"

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def record_use(self) -> None:
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])


class Pairing(models.Model):
    """One-time pairing code issued by an operator (CLI or Telegram).

    A connector client presents the code to /api/connectors/pair/claim,
    submits its public key, and receives its connector_id + key_id in return.
    The code is single-use and time-limited.
    """

    code = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=secrets.token_urlsafe,
    )
    tool = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    connector_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        state = "claimed" if self.claimed_at else "pending"
        return f"Pairing({self.code[:8]}… {state})"

    def is_claimable(self, now=None) -> bool:
        if now is None:
            now = timezone.now()
        return self.claimed_at is None and now < self.expires_at


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
