"""Management command: list active connectors or revoke one.

Usage:
    python manage.py orc_connectors list
    python manage.py orc_connectors revoke <connector_id>
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.connectors.models import ConnectorKey


class Command(BaseCommand):
    help = "List active connector keys or revoke a connector by id"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand")
        subparsers.add_parser("list", help="List all active connector keys")
        revoke_parser = subparsers.add_parser("revoke", help="Revoke a connector by id")
        revoke_parser.add_argument("connector_id", help="The connector_id to revoke")

    def handle(self, *args, **options):
        subcommand = options.get("subcommand")
        if subcommand == "list":
            self._list()
        elif subcommand == "revoke":
            self._revoke(options["connector_id"])
        else:
            self.print_help("manage.py", "orc_connectors")

    def _list(self):
        keys = ConnectorKey.objects.filter(revoked_at=None).order_by("connector_id")
        if not keys.exists():
            self.stdout.write("No active connector keys.")
            return

        self.stdout.write(
            f"{'CONNECTOR_ID':<30} {'KEY_ID':<10} {'TOOL':<16} {'LABEL':<20} {'LAST USED'}"
        )
        self.stdout.write("-" * 95)
        for key in keys:
            last_used = key.last_used_at.strftime("%Y-%m-%d %H:%M") if key.last_used_at else "never"
            self.stdout.write(
                f"{key.connector_id:<30} {key.key_id:<10} {key.tool:<16} {key.label:<20} {last_used}"
            )

    def _revoke(self, connector_id: str):
        try:
            key = ConnectorKey.objects.get(connector_id=connector_id, revoked_at=None)
        except ConnectorKey.DoesNotExist:
            raise CommandError(f"No active connector key found for id: {connector_id!r}") from None

        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
        self.stdout.write(self.style.SUCCESS(f"Revoked connector: {connector_id}"))
