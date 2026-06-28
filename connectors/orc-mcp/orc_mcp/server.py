"""MCP server exposing the OpenRemote-Control supervision tools.

Tools (the agent calls these; they route to the user's chat via the backend):
  - openremote_control(name)        -> start a session, dispatch it to the user's chat app
  - notify(message)                 -> push progress to chat
  - ask_human(question, options)    -> ask the user; returns their answer
  - request_approval(action, preview) -> returns 'allow' | 'deny' (fail-closed)

The `mcp` SDK is imported lazily inside _serve() so `orc_mcp.client` stays importable
(and testable) without the SDK installed.

CLI entry point: `orc-mcp [pair <code> [--backend URL] | serve]`
  orc-mcp serve  (default) — start the MCP server
  orc-mcp pair <code>       — claim a one-time pairing code, register Ed25519 key
"""

from __future__ import annotations

import sys


def _serve() -> None:
    import os

    from mcp.server.fastmcp import FastMCP

    from orc_mcp.client import OrcBackendClient

    client = OrcBackendClient()
    mcp = FastMCP("orc")

    @mcp.tool()
    def openremote_control(name: str = "") -> str:
        """Start an OpenRemote-Control session for this coding session and dispatch it to
        the operator's messaging app of choice (Telegram, WhatsApp, Slack, Signal, or
        iMessage), so they can supervise from their phone. Optionally pass a session name."""
        # Bind to THIS coding session so a Telegram reply resumes this exact
        # conversation (write+stream), not a fresh one. Claude Code exposes its
        # session id as CLAUDE_CODE_SESSION_ID to MCP-server subprocesses; cwd is
        # the workspace the resumed `claude -p` runs in.
        claude_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        try:
            workspace_root = os.getcwd()
        except OSError:
            workspace_root = ""
        result = client.start_remote_control(
            name,
            claude_session_id=claude_session_id,
            workspace_root=workspace_root,
        )
        if result.startswith("["):
            return result
        return f"Remote-control session dispatched to your chat: {result}"

    @mcp.tool()
    def notify(message: str) -> str:
        """Send a progress update to the operator's chat."""
        return "ok" if client.notify(message) else "failed"

    @mcp.tool()
    def ask_human(question: str, options: list[str] | None = None) -> str:
        """Ask the operator a question and return their answer (free text or chosen option)."""
        return client.ask(question, options or [])

    @mcp.tool()
    def request_approval(action: str, preview: str = "") -> str:
        """Request operator approval for a control action. Returns 'allow' or 'deny'."""
        return client.approve(action, preview)

    mcp.run()


def _pair(args: list[str]) -> None:
    import os

    from orc_mcp.pair import pair

    # Parse: pair <code> [--backend URL]
    code: str | None = None
    backend_url: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--backend" and i + 1 < len(args):
            backend_url = args[i + 1]
            i += 2
        elif code is None and not args[i].startswith("-"):
            code = args[i]
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not code:
        print("Usage: orc-mcp pair <code> [--backend URL]", file=sys.stderr)
        sys.exit(1)

    backend_url = backend_url or os.environ.get("ORC_BACKEND_URL") or "http://localhost:8000"

    try:
        result = pair(code, backend_url)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Paired successfully.")
    print(f"  connector_id : {result['connector_id']}")
    print(f"  key_id       : {result['key_id']}")
    print(f"  backend      : {backend_url}")
    print(f"Ed25519 key saved to ~/.config/openremote-control/connector_key")
    print(f"No shared secret needed — set ORC_BACKEND_URL, omit ORC_CONNECTOR_TOKEN.")


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "pair":
        _pair(argv[1:])
    elif not argv or argv[0] == "serve":
        _serve()
    else:
        print(f"Unknown subcommand: {argv[0]}", file=sys.stderr)
        print("Usage: orc-mcp [serve | pair <code> [--backend URL]]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
