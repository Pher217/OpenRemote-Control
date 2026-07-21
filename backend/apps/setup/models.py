"""Models for the first-run setup wizard.

Two objects, both deliberately small:

``SetupToken``
    The one-time credential that gates every ``/api/setup/*`` route. The
    installer opens the wizard at ``…/setup?token=<t>``; without a live token
    the routes are inert. Only the SHA-256 of the token is stored, so a
    database dump never yields a usable token.

``SetupState``
    A singleton row tracking how far the wizard has progressed. Once
    ``completed_at`` is set the whole setup surface is closed permanently
    (see ``apps.setup.auth``).

A dedicated token model is used rather than ``connectors.Pairing``: a Pairing
claim registers an Ed25519 connector public key and carries connector_id /
tool / scopes semantics. Overloading it for the wizard would couple two
unrelated lifecycles and pollute the connector audit trail.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

#: How long a freshly minted setup token stays usable. Short by default: the
#: installer opens the URL immediately, so a long window only widens the
#: exposure from browser history and access logs. `setup_token --ttl` raises it
#: for the operator who is re-opening setup and cannot act right away.
TOKEN_TTL = timedelta(minutes=30)

#: Name of the HttpOnly cookie the token is exchanged for on first load.
SESSION_COOKIE_NAME = "orc_setup_session"

#: Bytes of entropy per token (32 bytes -> 43-char urlsafe string).
TOKEN_BYTES = 32

#: Alphabet for the Telegram discovery challenge code. Excludes 0/O/1/I/L —
#: characters an operator reading the code off a screen and typing it into a
#: phone keyboard could easily transpose.
TELEGRAM_CHALLENGE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

#: How many characters follow the "ORC-" prefix.
TELEGRAM_CHALLENGE_LENGTH = 6


def hash_token(raw: str) -> str:
    """Return the hex SHA-256 of a raw token — what we persist and index on."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SetupToken(models.Model):
    """A single-use, time-limited credential for the setup wizard.

    The raw value exists only at mint time: :meth:`issue` returns it to the
    caller and nothing else ever stores it. Lookups go through the indexed
    hash, and the final comparison is constant-time.
    """

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SetupToken({self.token_hash[:8]}… {'consumed' if self.consumed_at else 'live'})"

    @classmethod
    def default_ttl(cls) -> timedelta:
        """TTL from settings, falling back to the module default."""
        minutes = getattr(settings, "ORC_SETUP_TOKEN_TTL_MINUTES", None)
        return timedelta(minutes=int(minutes)) if minutes else TOKEN_TTL

    @classmethod
    def issue(cls, *, ttl: timedelta | None = None) -> tuple[SetupToken, str]:
        """Mint a token, revoking any outstanding ones. Returns (obj, raw)."""
        ttl = ttl or cls.default_ttl()
        with transaction.atomic():
            cls.objects.filter(consumed_at__isnull=True).update(consumed_at=timezone.now())
            raw = secrets.token_urlsafe(TOKEN_BYTES)
            obj = cls.objects.create(
                token_hash=hash_token(raw),
                expires_at=timezone.now() + ttl,
            )
        return obj, raw

    @classmethod
    def verify(cls, raw: str) -> SetupToken | None:
        """Return the live token matching ``raw``, or None.

        The lookup is an exact match on an indexed SHA-256, which is what makes
        this safe: the raw token carries 256 bits of ``secrets`` entropy, and
        preimage resistance means an attacker cannot grind toward a matching
        index entry. (An additional ``compare_digest`` here would be theatre —
        the database already matched the column exactly, so the comparison
        could never fail.)
        """
        if not raw:
            return None
        candidate = cls.objects.filter(token_hash=hash_token(raw)).first()
        if candidate is None or not candidate.is_live():
            return None
        return candidate

    def is_live(self, now=None) -> bool:
        """True when the token is neither consumed nor expired."""
        if now is None:
            now = timezone.now()
        return self.consumed_at is None and now < self.expires_at

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])


class SetupState(models.Model):
    """Singleton row recording wizard progress.

    ``providers`` maps a provider key (``telegram``, ``whatsapp``, …) to its
    last known connection status; ``runtimes`` does the same for detected
    coding agents. Both are advisory display state — the authoritative
    configuration lives in ``deploy/.env`` and the connector tables.
    """

    STAGE_PROVIDERS = "providers"
    STAGE_RUNTIMES = "runtimes"
    STAGE_DONE = "done"
    STAGE_CHOICES = [
        (STAGE_PROVIDERS, "Connect chat providers"),
        (STAGE_RUNTIMES, "Connect agent runtimes"),
        (STAGE_DONE, "Complete"),
    ]

    #: Permitted stage edges. ``done`` is terminal over the network — only the
    #: operator's ``setup_token --reopen`` leaves it.
    ALLOWED_TRANSITIONS = {
        STAGE_PROVIDERS: {STAGE_RUNTIMES},
        STAGE_RUNTIMES: {STAGE_PROVIDERS, STAGE_DONE},
        STAGE_DONE: set(),
    }

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, default=STAGE_PROVIDERS)
    providers = models.JSONField(default=dict)
    runtimes = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    telegram_challenge = models.CharField(max_length=32, blank=True, default="")

    def __str__(self) -> str:
        return f"SetupState({self.stage})"

    @classmethod
    def load(cls) -> SetupState:
        """Return the singleton row, creating it on first access."""
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def connected_providers(self) -> list[str]:
        return sorted(k for k, v in self.providers.items() if v == "connected")

    def advance_to(self, stage: str) -> None:
        """Move the wizard to ``stage`` along an explicitly permitted edge.

        An arbitrary setter would let the wizard jump straight from providers to
        done, or wander back out of a terminal state and leave ``completed_at``
        set while ``stage`` said otherwise. The map keeps ``stage`` and
        ``completed_at`` a single invariant. Going back one step is allowed —
        the operator may want to revisit providers — but ``done`` is terminal.
        """
        if stage not in dict(self.STAGE_CHOICES):
            raise ValueError(f"unknown setup stage: {stage}")
        if stage not in self.ALLOWED_TRANSITIONS[self.stage]:
            raise ValueError(f"cannot move from {self.stage!r} to {stage!r}")
        if stage in (self.STAGE_RUNTIMES, self.STAGE_DONE) and not self.connected_providers:
            raise ValueError("at least one chat provider must be connected first")
        self.stage = stage
        if stage == self.STAGE_DONE:
            self.completed_at = timezone.now()
        self.save(update_fields=["stage", "completed_at", "updated_at"])

    def reopen(self) -> None:
        """Re-open a completed setup, clearing cached connection status.

        The provider/runtime maps are advisory display state captured at
        connection time. Carrying them across a reopen would let setup be
        re-completed immediately on the strength of statuses that may no longer
        be true, so they are cleared and must be re-established.
        """
        self.stage = self.STAGE_PROVIDERS
        self.completed_at = None
        self.providers = {}
        self.runtimes = {}
        # A completed setup implies a prior successful match already cleared the
        # challenge, so this is belt-and-suspenders — but clearing it here means
        # a reopened wizard never carries a stale code, independent of that
        # reasoning. A fresh code is minted when the operator re-validates.
        self.telegram_challenge = ""
        self.save(
            update_fields=[
                "stage",
                "completed_at",
                "providers",
                "runtimes",
                "telegram_challenge",
                "updated_at",
            ]
        )

    def set_provider(self, key: str, status: str) -> None:
        self.providers[key] = status
        self.save(update_fields=["providers", "updated_at"])

    def set_runtime(self, key: str, status: str) -> None:
        self.runtimes[key] = status
        self.save(update_fields=["runtimes", "updated_at"])

    def issue_telegram_challenge(self) -> str:
        """Mint and persist a fresh Telegram discovery challenge code.

        Telegram gives us no way to authenticate "the operator" — anyone who
        knows the bot's public username can add it to their own group and
        message it during the discovery window. The challenge code is the
        only trust anchor available: it is shown solely on the wizard page,
        so requiring it in the group message binds the discovered chat to
        someone who can actually see that page, not merely to whoever
        messages the bot first.
        """
        code = "ORC-" + "".join(
            secrets.choice(TELEGRAM_CHALLENGE_ALPHABET) for _ in range(TELEGRAM_CHALLENGE_LENGTH)
        )
        self.telegram_challenge = code
        self.save(update_fields=["telegram_challenge", "updated_at"])
        return code

    def clear_telegram_challenge(self) -> None:
        """Burn the challenge after a successful match — it is single-use.

        Leaving it set would keep a known-good code valid indefinitely. The
        code is visible to everyone in the operator's group once posted, so a
        re-run of discovery could then be satisfied by an attacker replaying
        that same code from a group of their own.
        """
        self.telegram_challenge = ""
        self.save(update_fields=["telegram_challenge", "updated_at"])
