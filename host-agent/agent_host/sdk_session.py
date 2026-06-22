"""sdk_session.py — run a Claude turn via the Agent SDK with human-gated tools.

Replaces the `claude -p --permission-mode bypassPermissions` headless engine with
the Claude Agent SDK so each tool use can be **approved from the operator's chat**
(Telegram Allow/Deny) instead of running unattended. Safe/auto-classified tools
(e.g. plain reads) flow without a prompt; permission-requiring tools invoke the
``can_use_tool`` callback, which asks the operator and blocks until they answer.

The approval mechanism is injected as ``approve(tool_name, tool_input, ctx) ->
bool`` so this module is unit-testable without a backend; the production approval
(host-authenticated connector approval → Telegram buttons) is supplied by the
daemon. Fail-closed: any approval error/timeout denies the tool.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

# approve(tool_name, tool_input, context) -> True to allow, False to deny.
ApproveFn = Callable[[str, dict, object], Awaitable[bool]]


def _permission_title(tool_name: str, tool_input: dict, ctx: object) -> str:
    """Human-readable approval line. Prefer the SDK-provided title, else build one."""
    title = getattr(ctx, "title", None)
    if title:
        return str(title)
    target = ""
    for key in ("file_path", "path", "command", "url", "pattern"):
        if isinstance(tool_input, dict) and tool_input.get(key):
            target = str(tool_input[key])
            break
    return f"Claude wants to use {tool_name}" + (f": {target}" if target else "")


async def run_turn(
    prompt: str,
    *,
    claude_session_id: str,
    cwd: str,
    started: bool,
    approve: ApproveFn,
    timeout: int = 1800,
) -> dict:
    """Run one Claude turn via the SDK; return {'text': str, 'is_error': bool}.

    Never raises. ``approve`` gates each permission-requiring tool; safe tools are
    auto-allowed by the CLI and never reach it. ``started`` resumes the session
    when True (else creates it with ``claude_session_id``).
    """
    import anyio
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
    )

    async def can_use_tool(tool_name, tool_input, ctx):
        # Fail-closed: deny on any approval error.
        try:
            allowed = await approve(tool_name, tool_input or {}, ctx)
        except Exception:
            log.exception("sdk_session: approval errored; denying %s", tool_name)
            allowed = False
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Denied by operator from chat.")

    # Do not load the operator's global allow-rules — they would auto-approve
    # tools and bypass the chat gate. Only the session itself drives permissions.
    opts = ClaudeAgentOptions(
        can_use_tool=can_use_tool,
        permission_mode="default",
        cwd=cwd,
        setting_sources=[],
        resume=claude_session_id if started else None,
    )

    text_parts: list[str] = []
    is_error = False
    try:
        with anyio.move_on_after(timeout) as scope:
            async with ClaudeSDKClient(options=opts) as client:
                await client.query(prompt)
                async for msg in client.receive_response():
                    for block in getattr(msg, "content", None) or []:
                        if type(block).__name__ == "TextBlock":
                            text_parts.append(getattr(block, "text", ""))
                result = getattr(msg, "result", None)  # ResultMessage carries final text
                if result:
                    text_parts.append(str(result))
        if scope.cancel_called:
            return {"text": f"(sdk turn timed out after {timeout}s)", "is_error": True}
    except Exception as exc:  # noqa: BLE001
        log.warning("sdk_session: turn failed: %s", exc)
        return {"text": f"(sdk turn failed: {exc})", "is_error": True}

    # Deduplicate: ResultMessage.result usually repeats the last TextBlock.
    text = text_parts[-1] if text_parts else ""
    return {"text": text, "is_error": is_error}
