import asyncio
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.observe.observer import (
    process_lines,
    read_new_lines,
    select_session_files,
)
from apps.observe.runtimes import iter_runtime_files


def _resolve_runtimes() -> list[str]:
    """Return the list of runtimes to observe.

    Priority:
    1. settings.OBSERVE_RUNTIMES  (new, list)
    2. [settings.OBSERVER_RUNTIME]  (legacy, single string)
    3. ['claude_code']  (hard default for backward-compat)
    """
    runtimes = getattr(settings, "OBSERVE_RUNTIMES", None)
    if runtimes:
        return list(runtimes)
    legacy = getattr(settings, "OBSERVER_RUNTIME", None)
    if legacy:
        return [legacy]
    return ["claude_code"]


class Command(BaseCommand):
    help = "Stream AI session transcripts from multiple runtimes and persist observed turns."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        runtimes = _resolve_runtimes()
        self.stdout.write(f"Observing runtimes: {', '.join(runtimes)}")

        forum_chat_id = getattr(settings, "TELEGRAM_FORUM_CHAT_ID", "")
        if forum_chat_id:
            self.stdout.write(f"observer: streaming to Telegram forum {forum_chat_id}")
        else:
            self.stdout.write(
                "observer: stdout mode (set TELEGRAM_FORUM_CHAT_ID to stream to Telegram)"
            )

        projects = [s.lower() for s in settings.OBSERVE_PROJECTS]
        active_minutes = settings.OBSERVE_ACTIVE_MINUTES
        projects_label = ", ".join(projects) if projects else "all"
        active_label = f"{active_minutes}" if active_minutes > 0 else "any"

        # Per-runtime state: offsets keyed by Path, seen sets keyed by (provider, session_key).
        # We use a flat per-runtime seen set (shared across files for that runtime) to mirror
        # the original single-runtime behaviour — dedup is by uuid within a provider.
        offsets: dict[tuple[str, Path], int] = {}
        seen: dict[str, set] = {rt: set() for rt in runtimes}
        # Per-file remembered session id (for runtimes whose turn lines lack one).
        file_states: dict[tuple[str, Path], dict] = {}
        last_selected: dict[str, int | None] = dict.fromkeys(runtimes)

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
                sid = p.get("session_id") or "?"
                self.stdout.write(
                    f"[{sid[:8]}] {p['role']}: {p['text'][:80]}"
                )

        while True:
            try:
                for provider in runtimes:
                    file_infos = iter_runtime_files(provider)
                    selected = select_session_files(
                        file_infos,
                        projects=projects,
                        active_minutes=active_minutes,
                        now_ts=time.time(),
                    )
                    total = len(file_infos)
                    sel_count = len(selected)
                    if sel_count != last_selected[provider]:
                        self.stdout.write(
                            f"observer[{provider}]: following {sel_count} of {total} sessions "
                            f"(projects={projects_label}, active<={active_label}min)"
                        )
                        last_selected[provider] = sel_count

                    for path in (Path(s) for s in selected):
                        key = (provider, path)
                        if key not in offsets:
                            # Seed at current end so only new turns stream.
                            offsets[key] = path.stat().st_size
                            continue
                        lines, new_offset = read_new_lines(path, offsets[key])
                        offsets[key] = new_offset
                        if lines:
                            seen[provider] = await process_lines(
                                lines,
                                str(path),
                                on_turn=on_turn,
                                seen=seen[provider],
                                provider=provider,
                                file_state=file_states.setdefault(key, {}),
                            )
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"observer scan error: {exc}")
            await asyncio.sleep(2)
