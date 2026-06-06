"""MCP server exposing the OpenRemote-Control supervision tools.

Tools (the agent calls these; they route to the user's chat via the backend):
  - notify(message)                 -> push progress to chat
  - ask_human(question, options)    -> ask the user; returns their answer
  - request_approval(action, preview) -> returns 'allow' | 'deny' (fail-closed)

The `mcp` SDK is imported lazily inside main() so `orc_mcp.client` stays importable
(and testable) without the SDK installed.
"""

from __future__ import annotations


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    from orc_mcp.client import OrcBackendClient

    client = OrcBackendClient()
    mcp = FastMCP("orc")

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


if __name__ == "__main__":
    main()
