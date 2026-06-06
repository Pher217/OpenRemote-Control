import os
from pathlib import Path

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


async def process_lines(lines, jsonl_path, *, on_turn, seen=None, provider=None, file_state=None):
    """Parse + persist new transcript lines for one file.

    Runtimes differ in what each turn line carries: Claude Code lines have both a
    per-turn `uuid` and a `session_id`; Codex/Gemini turn lines have neither (the
    session id only appears once in a header line). So:

    - Session key per file: prefer the turn's own session_id, else the session id
      remembered from a header line (carried across polls via `file_state`), else the
      file stem. One file == one observed session, consistently across both branches.
    - Dedup: only by `uuid` when present (Claude Code replays lines); when a runtime
      has no uuids, the offset-based new-line reading already prevents reprocessing,
      so a None uuid is never added to `seen` (avoids a None collision dropping turns).
    """
    if seen is None:
        seen = set()
    if file_state is None:
        file_state = {}
    runtime = provider or settings.OBSERVER_RUNTIME
    adapter = get_runtime_adapter(runtime)
    for raw in lines:
        meta = adapter.extract_session_meta(raw)
        meta_session = meta.pop("session_id", None)
        if meta_session:
            file_state["session"] = meta_session
        if meta and meta_session:
            thread = await database_sync_to_async(get_or_create_observed_thread)(
                meta_session, jsonl_path, runtime
            )
            await database_sync_to_async(apply_session_meta)(thread, meta)

        p = adapter.parse_turn(raw)
        if p is None:
            continue
        uuid = p.get("uuid")
        if uuid is not None and uuid in seen:
            continue
        session_ref = p.get("session_id") or file_state.get("session") or Path(jsonl_path).stem
        thread = await database_sync_to_async(get_or_create_observed_thread)(
            session_ref, jsonl_path, runtime
        )
        msg = await record_turn(thread, p["role"], p["text"])
        if uuid is not None:
            seen.add(uuid)
        await on_turn(thread, p, msg)
    return seen
