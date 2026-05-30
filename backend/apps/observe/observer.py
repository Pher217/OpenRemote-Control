from channels.db import database_sync_to_async

from apps.observe.parser import parse_line
from apps.observe.service import get_or_create_observed_thread, record_turn


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


async def process_lines(lines, jsonl_path, *, on_turn, seen=None):
    if seen is None:
        seen = set()
    for raw in lines:
        p = parse_line(raw)
        if p is None or p["uuid"] in seen:
            continue
        thread = await database_sync_to_async(get_or_create_observed_thread)(
            p["session_id"], jsonl_path
        )
        msg = await record_turn(thread, p["role"], p["text"])
        seen.add(p["uuid"])
        await on_turn(thread, p, msg)
    return seen
