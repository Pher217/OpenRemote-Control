# orc-mcp — OpenRemote-Control universal MCP bridge

`orc-mcp` is purely a **dispatch tool**: install this MCP server into **any MCP-capable
coding tool** and the agent gains four calls that route through your sovereign
OpenRemote-Control backend into the chat app you already use (Telegram/Matrix):

- `openremote_control(name)` — start a session and dispatch it to your chat app so you can supervise from your phone (this is the universal `/openremote-control` command).
- `notify(message)` — push progress to your chat.
- `ask_human(question, options)` — ask you a question; the agent waits for your reply.
- `request_approval(action, preview)` — ask you to allow/deny a control action (**fail-closed**: denies on timeout).

It calls only your backend's `/api/connectors/*` endpoints — no vendor APIs, no scraping.
This is opt-in: the agent calls these tools; we never puppet a closed UI.

## The `/openremote-control` command in Claude Code

A ready-made slash command ships at [`claude/openremote-control.md`](claude/openremote-control.md).
Drop it into your Claude Code commands so you can type the command in the coding chat:

```bash
mkdir -p ~/.claude/commands
cp connectors/orc-mcp/claude/openremote-control.md ~/.claude/commands/
```

Then `/openremote-control [name]` inside Claude Code calls the `openremote_control` tool,
which dispatches the session to your messaging app. Other MCP tools call the tool directly.

## Auth — Ed25519 per-connector identity (recommended)

Each install generates its own Ed25519 keypair. The backend stores only the **public key**
(registered via a one-time pairing code). No shared secret is stored anywhere.

### One-time pairing

After `pipx install`, run once:

```bash
orc-mcp pair <code> --backend https://your.orc-backend.example
```

`<code>` comes from the backend:

- Django management command: `python manage.py orc_pair`
- Telegram bot: send `/pair` — the bot replies with a QR code and a text code.

On success, the connector prints its `connector_id` and stores:

```
~/.config/openremote-control/connector_key   # PEM Ed25519 private key, mode 0600
~/.config/openremote-control/connector.json  # connector_id, key_id, backend_url
```

Then set `ORC_BACKEND_URL` (if not already passed with `--backend`) and start the MCP server:

```bash
export ORC_BACKEND_URL=https://your.orc-backend.example
orc-mcp serve   # or just: orc-mcp
```

No `ORC_CONNECTOR_TOKEN` needed — the identity file replaces the shared secret.

### How signing works

For every backend request the connector adds five headers:

| Header | Contents |
|--------|----------|
| `X-ORC-Connector-Id` | stable connector identity |
| `X-ORC-Key-Id` | which public key to verify with |
| `X-ORC-Timestamp` | Unix timestamp (seconds) |
| `X-ORC-Nonce` | 16-char hex one-time value |
| `X-ORC-Signature` | standard-base64 Ed25519 signature |

The signature covers the canonical message (UTF-8 bytes):

```
METHOD\nPATH\nsha256hex(body)\nTIMESTAMP\nNONCE
```

The backend verifies this against the stored public key — replay attacks are blocked by
the nonce and a short timestamp window.

## Auth — legacy bearer token (fallback)

If no identity file exists and `ORC_CONNECTOR_TOKEN` is set, the connector falls back
to `Authorization: Bearer <token>`. This is the v0.1 mode; it keeps working but is
less secure (shared secret).

## Env vars

| Var | Required | Default | Meaning |
|-----|----------|---------|---------|
| `ORC_BACKEND_URL` | yes | `http://localhost:8000` | Your OpenRemote-Control backend base URL (over Tailscale/headscale for remote). |
| `ORC_CONNECTOR_TOKEN` | legacy only | — | Bearer token fallback (ignored when an Ed25519 identity exists). |
| `ORC_CONNECTOR_ID` | no | `orc-<hostname>` | Overrides connector id when no identity file is present. |
| `ORC_TOOL` | no | `unknown` | Which tool this is (e.g. `cursor`, `claude_code`). Used as a label on pairing. |

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

Set `ORC_BACKEND_URL` in your shell profile (or the tool's MCP `env` block). Then tell the
agent (via a rule / `AGENTS.md` / system prompt) to use `ask_human` when it needs a
decision and `request_approval` before risky actions.

## Install

```bash
pipx install ./connectors/orc-mcp   # or: uv tool install ./connectors/orc-mcp
orc-mcp pair <code> --backend https://your.orc-backend.example
```

## Honest limits

Reaches any tool that loads a custom MCP server (most do). It does **not** puppet a
closed first-party GUI chat (Copilot Chat's box, Cursor/Windsurf Cascade UI) or a
cloud web UI — those are reached only by the agent opting into these tools.
