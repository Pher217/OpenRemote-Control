"""close_stale_topics — close Telegram forum topics for completed threads.

Usage: python manage.py close_stale_topics [--dry-run]
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.threads.models import Thread


class Command(BaseCommand):
    help = 'Close Telegram forum topics for threads in COMPLETED status.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be closed without making Telegram API calls.',
        )

    def handle(self, *args, **options):
        import asyncio

        from apps.telegram import telegram_api

        dry_run = options['dry_run']
        default_chat_id_raw = getattr(settings, 'TELEGRAM_FORUM_CHAT_ID', '')
        if not default_chat_id_raw:
            self.stderr.write('TELEGRAM_FORUM_CHAT_ID not configured — nothing to do.')
            return

        try:
            default_chat_id = int(default_chat_id_raw)
        except (TypeError, ValueError):
            self.stderr.write(f'TELEGRAM_FORUM_CHAT_ID is not a valid integer: {default_chat_id_raw!r}')
            return

        candidates = Thread.objects.filter(status=Thread.StatusChoices.COMPLETED)
        closed = 0
        skipped = 0

        for thread in candidates.iterator():
            md = thread.metadata or {}
            topic_id = md.get('telegram_topic_id')
            if not topic_id:
                skipped += 1
                continue
            # Prefer the chat_id stored when the topic was created so threads from
            # a previous forum are closed in the right group, not the current one.
            stored_chat_raw = md.get('telegram_forum_chat_id')
            try:
                chat_id = int(stored_chat_raw) if stored_chat_raw else default_chat_id
            except (TypeError, ValueError):
                chat_id = default_chat_id
            label = md.get('title') or thread.external_session_ref[:16]
            if dry_run:
                self.stdout.write(f'[dry-run] would close topic {topic_id} (chat {chat_id}) for thread {thread.id} ({label})')
                closed += 1
                continue
            try:
                asyncio.run(telegram_api.close_forum_topic(chat_id, topic_id))
                new_md = dict(md)
                for k in ('telegram_topic_id', 'telegram_digest_message_id', 'telegram_digest_steps'):
                    new_md.pop(k, None)
                Thread.objects.filter(id=thread.id).update(metadata=new_md)
                self.stdout.write(f'Closed topic {topic_id} for thread {thread.id} ({label})')
                closed += 1
            except Exception as exc:
                self.stderr.write(f'Failed to close topic {topic_id} for thread {thread.id}: {exc}')

        self.stdout.write(f'Done: {closed} closed, {skipped} skipped (no topic).')
