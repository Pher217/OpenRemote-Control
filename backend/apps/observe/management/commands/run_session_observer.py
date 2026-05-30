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

        forum_chat_id = getattr(settings, "TELEGRAM_FORUM_CHAT_ID", "")
        if forum_chat_id:
            self.stdout.write(f"observer: streaming to Telegram forum {forum_chat_id}")
        else:
            self.stdout.write(
                "observer: stdout mode (set TELEGRAM_FORUM_CHAT_ID to stream to Telegram)"
            )

        offsets: dict[Path, int] = {}
        seen: set = set()

        async def on_turn(thread, p, msg):
            if forum_chat_id:
                from apps.observe.delivery import deliver_turn
                from apps.telegram.telegram_api import redact_token

                try:
                    await deliver_turn(
                        thread, p, msg, forum_chat_id=int(forum_chat_id)
                    )
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(f"observer deliver error: {redact_token(str(exc))}")
            else:
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
