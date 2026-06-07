"""Management command: create a pairing code and display the QR code.

Usage:
    python manage.py orc_pair --tool cursor --label "laptop" [--ttl 900] [--backend https://orc.example.com]

Prints:
  - The short pairing code
  - A terminal QR code encoding the pairing payload
  - The exact client command to run: orc-mcp pair <code> --backend <url>
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.connectors.models import Pairing
from apps.connectors.qr import pairing_payload, terminal_qr


class Command(BaseCommand):
    help = "Create a one-time pairing code and display the terminal QR for connector enrollment"

    def add_arguments(self, parser):
        parser.add_argument("--tool", default="", help="Tool identifier (e.g. cursor, claude_code)")
        parser.add_argument("--label", default="", help="Human-readable label for this connector")
        parser.add_argument("--ttl", type=int, default=900, help="Code validity in seconds (default 900)")
        parser.add_argument(
            "--backend",
            default="",
            help="Backend URL the client should connect to (overrides ORC_PUBLIC_BASE_URL)",
        )

    def handle(self, *args, **options):
        tool = options["tool"]
        label = options["label"]
        ttl = options["ttl"]
        backend_url = options["backend"] or getattr(settings, "ORC_PUBLIC_BASE_URL", "")

        now = timezone.now()
        pairing = Pairing.objects.create(
            tool=tool,
            label=label,
            expires_at=now + timedelta(seconds=ttl),
        )

        payload = pairing_payload(pairing.code, backend_url)
        qr = terminal_qr(payload)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Connector Pairing ==="))
        self.stdout.write(f"  Code   : {pairing.code}")
        self.stdout.write(f"  Tool   : {tool or '(any)'}")
        self.stdout.write(f"  Label  : {label or '(none)'}")
        self.stdout.write(f"  Expires: {pairing.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')} ({ttl}s)")
        self.stdout.write("")
        self.stdout.write(qr)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Run on the client:"))
        if backend_url:
            self.stdout.write(f"  orc-mcp pair {pairing.code} --backend {backend_url}")
            self.stdout.write(f"  orc-host pair {pairing.code} --backend {backend_url}  (for host daemons)")
        else:
            self.stdout.write(f"  orc-mcp pair {pairing.code}")
            self.stdout.write(f"  orc-host pair {pairing.code}  (for host daemons)")
        self.stdout.write("")
