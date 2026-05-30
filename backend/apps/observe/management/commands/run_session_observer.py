import asyncio
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.observe.observer import process_lines, read_new_lines


class Command(BaseCommand):
    help = "Stream Claude Code session transcripts and persist surfaced turns as observed threads."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        configured = getattr(settings, "OBSERVE_CLAUDE_PROJECTS_DIR", None)
        projects_dir = Path(configured) if configured else Path.home() / ".claude" / "projects"
        self.stdout.write(f"Observing Claude Code sessions in {projects_dir}")

        offsets: dict[Path, int] = {}
        seen: set = set()

        async def on_turn(thread, p, msg):
            self.stdout.write(f"[{p['session_id'][:8]}] {p['role']}: {p['text'][:80]}")

        while True:
            try:
                for path in projects_dir.glob("**/*.jsonl"):
                    if path not in offsets:
                        # Seed new files at their current end so only NEW turns stream.
                        offsets[path] = path.stat().st_size
                        continue
                    lines, new_offset = read_new_lines(path, offsets[path])
                    offsets[path] = new_offset
                    if lines:
                        seen = await process_lines(
                            lines, str(path), on_turn=on_turn, seen=seen
                        )
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"observer scan error: {exc}")
            await asyncio.sleep(2)
