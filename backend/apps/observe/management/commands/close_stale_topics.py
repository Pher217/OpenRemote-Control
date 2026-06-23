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
        forum_chat_id = getattr(settings, 'TELEGRAM_FORUM_CHAT_ID', '')
        if not forum_chat_id:
            self.stderr.write('TELEGRAM_FORUM_CHAT_ID not configured — nothing to do.')
            return

        forum_chat_id = int(forum_chat_id)
        candidates = Thread.objects.filter(status=Thread.StatusChoices.COMPLETED)
        closed = 0
        skipped = 0

        for thread in candidates.iterator():
            topic_id = (thread.metadata or {}).get('telegram_topic_id')
            if not topic_id:
                skipped += 1
                continue
            label = (thread.metadata or {}).get('title') or thread.external_session_ref[:16]
            if dry_run:
                self.stdout.write(f'[dry-run] would close topic {topic_id} for thread {thread.id} ({label})')
                closed += 1
                continue
            try:
                asyncio.run(telegram_api.close_forum_topic(forum_chat_id, topic_id))
                md = dict(thread.metadata)
                for k in ('telegram_topic_id', 'telegram_digest_message_id', 'telegram_digest_steps'):
                    md.pop(k, None)
                Thread.objects.filter(id=thread.id).update(metadata=md)
                self.stdout.write(f'Closed topic {topic_id} for thread {thread.id} ({label})')
                closed += 1
            except Exception as exc:
                self.stderr.write(f'Failed to close topic {topic_id} for thread {thread.id}: {exc}')

        self.stdout.write(f'Done: {closed} closed, {skipped} skipped (no topic).')
