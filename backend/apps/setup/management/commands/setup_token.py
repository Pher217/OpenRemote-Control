"""Mint a setup-wizard token and print the URL the installer should open.

Run by ``quickstart.sh`` after the stack is healthy, and by an operator who
needs to re-open setup later. Issuing a token revokes any outstanding one, so
only the most recently printed URL ever works.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.setup.models import SetupState, SetupToken


class Command(BaseCommand):
    help = "Issue a one-time setup token and print the wizard URL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reopen",
            action="store_true",
            help="Re-open a completed setup (clears the completion flag).",
        )
        parser.add_argument(
            "--url-only",
            action="store_true",
            help="Print just the URL, for scripting.",
        )
        parser.add_argument(
            "--ttl",
            type=int,
            default=None,
            metavar="MINUTES",
            help="Token lifetime in minutes (default: ORC_SETUP_TOKEN_TTL_MINUTES).",
        )

    def handle(self, *args, **options):
        state = SetupState.load()
        if state.is_complete:
            if not options["reopen"]:
                self.stderr.write(
                    self.style.ERROR(
                        "Setup is already complete. Re-run with --reopen to issue a new token."
                    )
                )
                return
            state.reopen()

        ttl = timedelta(minutes=options["ttl"]) if options["ttl"] else None
        _, raw = SetupToken.issue(ttl=ttl)
        base = settings.ORC_SETUP_BASE_URL.rstrip("/")
        url = f"{base}/setup?token={raw}"

        if options["url_only"]:
            self.stdout.write(url)
            return

        minutes = options["ttl"] or settings.ORC_SETUP_TOKEN_TTL_MINUTES
        self.stdout.write(self.style.SUCCESS("Setup wizard ready — open this URL:"))
        self.stdout.write(f"\n  {url}\n")
        self.stdout.write(
            f"Expires in {minutes} minutes. Opening it swaps the token for a "
            "browser session, so the link above stops working immediately after "
            "first use. Do not expose this port publicly."
        )
