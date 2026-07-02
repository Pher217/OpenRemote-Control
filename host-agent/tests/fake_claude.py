#!/usr/bin/env python3
"""Stand-in for the claude binary speaking the stream-json protocol.

Used by tests via ORC_CLAUDE_BIN. Behavior controlled by FAKE_CLAUDE_MODE env
var: 'echo' (default), 'crash_after_first', or 'tool_step'.
"""
from __future__ import annotations

import json
import os
import sys


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def main():
    mode = os.environ.get("FAKE_CLAUDE_MODE", "echo")
    resumed = "--resume" in sys.argv
    session_id = "fake-resumed" if resumed else "fake-sess"
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session_id}), flush=True)

    turn = 0
    for line in sys.stdin:
        turn += 1
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = _extract_text(ev.get("message", {}).get("content", ""))
        reply = f"echo:{turn}: {text}"

        if mode == "tool_step":
            tool_block = {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}
            print(
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [tool_block]}}),
                flush=True,
            )

        print(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}}),
            flush=True,
        )
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False}), flush=True)

        if mode == "crash_after_first" and turn == 1:
            sys.exit(1)


if __name__ == "__main__":
    main()
