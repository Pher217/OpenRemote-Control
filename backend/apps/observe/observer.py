import os

from channels.db import database_sync_to_async
from django.conf import settings

from apps.observe.runtimes import get_runtime_adapter
from apps.observe.service import (
    apply_session_meta,
    get_or_create_observed_thread,
    record_turn,
)


def select_session_files(file_infos, *, projects, active_minutes, now_ts):
    """file_infos: list of (path:str, mtime:float). Returns the filtered list of paths.

    - projects: list of lowercase substrings; if non-empty, keep a file only when one of them is a
      substring of its PARENT-DIR name (the project slug), case-insensitive. Empty list => keep all.
    - active_minutes: if > 0, keep only files with (now_ts - mtime) <= active_minutes*60. 0 => keep all.
    """
    selected = []
    for path, mtime in file_infos:
        if projects:
            slug = os.path.basename(os.path.dirname(path)).lower()
            if not any(sub in slug for sub in projects):
                continue
        if active_minutes > 0 and (now_ts - mtime) > active_minutes * 60:
            continue
        selected.append(path)
    return selected


def read_new_lines(path, offset) -> tuple[list[str], int]:
    with open(path, encoding="utf-8") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    last_nl = data.rfind("\n")
    if last_nl == -1:
        return [], offset
    complete = data[: last_nl + 1]
    lines = complete.splitlines()
    new_offset = offset + len(complete.encode("utf-8"))
    return lines, new_offset


async def process_lines(lines, jsonl_path, *, on_turn, seen=None, provider=None):
    if seen is None:
        seen = set()
    runtime = provider or settings.OBSERVER_RUNTIME
    adapter = get_runtime_adapter(runtime)
    for raw in lines:
        meta = adapter.extract_session_meta(raw)
        meta_session = meta.pop("session_id", None)
        if meta and meta_session:
            thread = await database_sync_to_async(get_or_create_observed_thread)(
                meta_session, jsonl_path, runtime
            )
            await database_sync_to_async(apply_session_meta)(thread, meta)

        p = adapter.parse_turn(raw)
        if p is None or p["uuid"] in seen:
            continue
        thread = await database_sync_to_async(get_or_create_observed_thread)(
            p["session_id"], jsonl_path, runtime
        )
        msg = await record_turn(thread, p["role"], p["text"])
        seen.add(p["uuid"])
        await on_turn(thread, p, msg)
    return seen
