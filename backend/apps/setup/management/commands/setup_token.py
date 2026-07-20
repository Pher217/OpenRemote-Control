"""Mint a setup-wizard token and print the URL the installer should open.

Run by ``quickstart.sh`` after the stack is healthy, and by an operator who
needs to re-open setup later. Issuing a token revokes any outstanding one, so
only the most recently printed URL ever works.
"""

from __future__ import annotations

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
            state.completed_at = None
            state.stage = SetupState.STAGE_PROVIDERS
            state.save(update_fields=["completed_at", "stage", "updated_at"])

        _, raw = SetupToken.issue()
        base = settings.ORC_SETUP_BASE_URL.rstrip("/")
        url = f"{base}/setup?token={raw}"

        if options["url_only"]:
            self.stdout.write(url)
            return

        self.stdout.write(self.style.SUCCESS("Setup token issued."))
        self.stdout.write(f"\n  {url}\n")
        self.stdout.write(
            "Single-use, expires in 24 hours. Do not expose this port publicly.\n"
            "Note: the /setup page itself is not built yet — until it ships, the "
            "token is for the /api/setup/* endpoints."
        )
