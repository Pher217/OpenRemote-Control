#!/usr/bin/env python3
"""Executable + importable stand-in for the codex binary speaking the JSONL
event protocol. Used by tests via ORC_CODEX_BIN. Behavior controlled by
FAKE_CODEX_MODE env var: 'echo' (default), 'crash', or 'tool_step'.

Called as: exec [resume <id>] --json --skip-git-repo-check [--sandbox <m>] <prompt>
The engine always places the prompt as the LAST argv token.
"""
from __future__ import annotations

import json
import os
import sys


def _prompt_from_argv(argv):
    # The engine always puts the prompt last (both fresh and resume).
    return argv[-1] if len(argv) > 1 else ""


def main():
    mode = os.environ.get("FAKE_CODEX_MODE", "echo")
    resumed = "resume" in sys.argv
    thread_id = "codex-resumed" if resumed else "codex-fresh"
    print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)

    if mode == "crash":
        sys.exit(1)

    prompt = _prompt_from_argv(sys.argv)
    if mode == "tool_step":
        print(
            json.dumps(
                {"type": "item.completed", "item": {"type": "command_execution", "text": "ls"}}
            ),
            flush=True,
        )
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "echo:" + prompt},
            }
        ),
        flush=True,
    )
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)


if __name__ == "__main__":
    main()
