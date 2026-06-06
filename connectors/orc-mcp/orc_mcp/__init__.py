"""orc-mcp — the OpenRemote-Control universal MCP bridge.

A small MCP server the user installs into any MCP-capable coding tool so the agent
can reach the user's chat: notify / ask_human / request_approval route to the
sovereign OpenRemote-Control backend, which delivers a Prompt to Telegram/Matrix
and returns the answer.
"""

__all__ = ["OrcBackendClient"]

from orc_mcp.client import OrcBackendClient
