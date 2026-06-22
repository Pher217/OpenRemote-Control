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


def make_approve(backend_url: str, host_token: str, thread_id: str, *, poll_interval: float = 2.0, timeout: float = 1700.0) -> ApproveFn:
    """Build an ``approve`` callback that asks the operator via the session topic.

    Posts the tool request to the host-authenticated approval endpoint (which
    delivers Allow/Deny buttons to the session's Telegram topic), then polls for
    the decision. Fail-closed: any error / timeout / expiry → deny.
    """
    import httpx

    base = backend_url.rstrip("/")
    headers = {"Authorization": f"Bearer {host_token}"}

    async def approve(tool_name: str, tool_input: dict, ctx: object) -> bool:
        title = _permission_title(tool_name, tool_input, ctx)
        preview = _input_preview(tool_input)
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    f"{base}/api/hostlink/approve",
                    json={"thread_id": thread_id, "title": title, "preview": preview},
                    headers=headers,
                )
                r.raise_for_status()
                nonce = r.json()["nonce"]
                import anyio
                deadline = timeout
                elapsed = 0.0
                while elapsed < deadline:
                    pr = await c.get(f"{base}/api/hostlink/approve/{nonce}", headers=headers)
                    pr.raise_for_status()
                    data = pr.json()
                    st = data.get("status")
                    if st == "answered":
                        return data.get("decision") == "allow"
                    if st in ("expired", "cancelled"):
                        return False
                    await anyio.sleep(poll_interval)
                    elapsed += poll_interval
        except Exception:
            log.exception("sdk_session: approval request failed; denying %s", tool_name)
            return False
        return False  # timed out → deny

    return approve


def _input_preview(tool_input: dict, limit: int = 300) -> str:
    """A short, safe preview of the tool input for the approval body."""
    import json as _json

    try:
        s = _json.dumps(tool_input, ensure_ascii=False)
    except Exception:
        s = str(tool_input)
    return s[:limit] + ("…" if len(s) > limit else "")


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

    def _opts(mode: str) -> ClaudeAgentOptions:
        # Do not load the operator's global allow-rules — they would auto-approve
        # tools and bypass the chat gate. Only the session itself drives permissions.
        # mode "create": pin the session to our id (--session-id); mode "resume":
        # continue it (--resume). Pinning on creation is what makes a later resume
        # find the conversation (the bug: resume=None created an SDK-chosen id).
        kw = {"session_id": claude_session_id} if mode == "create" else {"resume": claude_session_id}
        return ClaudeAgentOptions(
            can_use_tool=can_use_tool,
            permission_mode="default",
            cwd=cwd,
            setting_sources=[],
            **kw,
        )

    async def _attempt(mode: str) -> dict:
        text_parts: list[str] = []
        with anyio.move_on_after(timeout) as scope:
            async with ClaudeSDKClient(options=_opts(mode)) as client:
                await client.query(prompt)
                async for msg in client.receive_response():
                    for block in getattr(msg, "content", None) or []:
                        if type(block).__name__ == "TextBlock":
                            text_parts.append(getattr(block, "text", ""))
                    result = getattr(msg, "result", None)  # ResultMessage final text
                    if result:
                        text_parts.append(str(result))
        if scope.cancel_called:
            return {"text": f"(sdk turn timed out after {timeout}s)", "is_error": True}
        # Deduplicate: ResultMessage.result usually repeats the last TextBlock.
        return {"text": text_parts[-1] if text_parts else "", "is_error": False}

    # Resume-or-create with fallback: `started` is a hint, not truth (a daemon
    # restart desyncs it). Try the hinted mode, fall back to the other — create a
    # fresh session if resume can't find it, or resume if create says it exists.
    order = ["resume", "create"] if started else ["create", "resume"]
    last_exc: Exception | None = None
    for mode in order:
        try:
            return await _attempt(mode)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("sdk_session: turn (%s) failed: %s", mode, exc)
    return {"text": f"(sdk turn failed: {last_exc})", "is_error": True}
