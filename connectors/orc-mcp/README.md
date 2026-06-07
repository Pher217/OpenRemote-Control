# orc-mcp — OpenRemote-Control universal MCP bridge

`orc-mcp` is purely a **dispatch tool**: install this MCP server into **any MCP-capable
coding tool** and the agent gains three calls that route through your sovereign
OpenRemote-Control backend into the chat app you already use (Telegram/Matrix):

- `notify(message)` — push progress to your chat.
- `ask_human(question, options)` — ask you a question; the agent waits for your reply.
- `request_approval(action, preview)` — ask you to allow/deny a control action (**fail-closed**: denies on timeout).

It calls only your backend's `/api/connectors/*` endpoints — no vendor APIs, no scraping.
This is opt-in: the agent calls these tools; we never puppet a closed UI.

## Env vars

| Var | Required | Default | Meaning |
|-----|----------|---------|---------|
| `ORC_BACKEND_URL` | yes | `http://localhost:8000` | Your OpenRemote-Control backend base URL (over Tailscale/headscale for remote). |
| `ORC_CONNECTOR_TOKEN` | yes | — | Bearer token; must match the backend's `ORC_CONNECTOR_TOKEN`. |
| `ORC_CONNECTOR_ID` | no | `orc-<hostname>` | Stable id for this install (binds the session). |
| `ORC_TOOL` | no | `unknown` | Which tool this is (e.g. `cursor`, `claude_code`). |

## One-line install per tool

**Claude Code**
```
claude mcp add orc -- orc-mcp
```
**OpenAI Codex CLI** — `~/.codex/config.toml`
```toml
[mcp_servers.orc]
command = "orc-mcp"
```
**Cursor** — `~/.cursor/mcp.json`
```json
{ "mcpServers": { "orc": { "command": "orc-mcp" } } }
```
**Windsurf** — `~/.codeium/windsurf/mcp_config.json`
```json
{ "mcpServers": { "orc": { "command": "orc-mcp" } } }
```
**VS Code (GitHub Copilot)** — `.vscode/mcp.json`
```json
{ "servers": { "orc": { "command": "orc-mcp" } } }
```
**Gemini CLI** — `~/.gemini/settings.json`
```json
{ "mcpServers": { "orc": { "command": "orc-mcp" } } }
```
**OpenCode** — `opencode.json`
```json
{ "mcp": { "orc": { "type": "local", "command": ["orc-mcp"] } } }
```

Set the env vars in your shell profile (or the tool's MCP `env` block). Then tell the
agent (via a rule / `AGENTS.md` / system prompt) to use `ask_human` when it needs a
decision and `request_approval` before risky actions.

## Install

```
pipx install ./connectors/orc-mcp   # or: uv tool install ./connectors/orc-mcp
```

## Honest limits

Reaches any tool that loads a custom MCP server (most do). It does **not** puppet a
closed first-party GUI chat (Copilot Chat's box, Cursor/Windsurf Cascade UI) or a
cloud web UI — those are reached only by the agent opting into these tools.
