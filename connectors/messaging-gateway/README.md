# orc-messaging-gateway

A Node/TypeScript sidecar that bridges WhatsApp, Slack, Discord, Signal, and iMessage **directly**
(no Matrix relay) to the OpenRemote-Control backend.

Inspired by [pi-messenger-bridge](https://github.com/tintinweb/pi-messenger-bridge) (MIT) —
same library choices, fresh implementation decoupled from the `pi` agent.

---

## What it does

- Polls `GET /api/gateway/outbox?platform=<p>&max=20` on the backend for outbound messages and
  delivers them via the appropriate platform adapter.
- On every inbound user message, posts `POST /api/gateway/inbound` to the backend; if the backend
  returns a non-null `reply`, sends it back to the user on the same platform.
- Each platform adapter runs in isolation — one failing adapter does not stop the others.

---

## Single messaging app of choice

The backend is configured with a single `ORC_MESSAGING_PLATFORM` setting that
names the one app where all notifications and approval prompts are delivered.
`ENABLED_PLATFORMS` in this sidecar should match that choice — if the backend
is set to `whatsapp`, set `ENABLED_PLATFORMS=whatsapp`.  Running additional
platforms (e.g. `whatsapp,slack`) is harmless but those extra adapters will
start, poll, and receive nothing, since the backend only sends to the one
configured platform.

---

## Per-platform setup

### WhatsApp

WhatsApp uses the [Baileys](https://github.com/WhiskeySockets/Baileys) library which speaks the
WhatsApp Web multi-device protocol. **No token required** — authentication is via QR code.

1. Start the gateway with `whatsapp` in `ENABLED_PLATFORMS`.
2. On first run a QR code is printed to the terminal.
3. Open WhatsApp on your phone → Settings → Linked Devices → Link a Device → scan the QR.
4. Credentials are saved to `./data/whatsapp/` and reused on subsequent starts.

**WhatsApp ban-risk caveat:** Baileys uses the unofficial WhatsApp Web protocol on a personal
account. WhatsApp may ban accounts that send high volumes of automated messages or that violate
their Terms of Service. Use a dedicated account and keep volumes reasonable.
This gateway relays text messages only; media is silently ignored.

Outbox recipient format: `<countrycode><number>@s.whatsapp.net` for individuals,
`<groupId>@g.us` for groups.

### Slack

Uses [@slack/bolt](https://github.com/slackapi/bolt-js) in Socket Mode (no public HTTPS
endpoint required).

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app.
2. Enable **Socket Mode** (Settings → Socket Mode → Enable). Copy the **App-Level Token**
   (`xapp-…`) — set as `SLACK_APP_TOKEN`.
3. Under **OAuth & Permissions**, add these Bot Token Scopes:
   - `channels:history` / `groups:history` — read messages
   - `chat:write` — send messages
4. Under **Event Subscriptions → Subscribe to Bot Events**, add `message.channels` (and
   `message.groups` for private channels).
5. Install the app to your workspace. Copy the **Bot User OAuth Token** (`xoxb-…`) — set as
   `SLACK_BOT_TOKEN`.
6. Invite the bot to the channels it should monitor.

Outbox recipient: Slack channel ID (e.g. `C0123456789`).

### Discord

Uses [discord.js](https://github.com/discordjs/discord.js) with Guilds + GuildMessages +
MessageContent intents.

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and
   create a new application → Bot.
2. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**.
3. Copy the bot token — set as `DISCORD_TOKEN`.
4. Generate an invite URL under **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Read Messages/View Channels`, `Send Messages`
5. Use the URL to invite the bot to your server.

Outbox recipient: Discord channel ID (right-click channel → Copy Channel ID).

### Signal

Uses [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) — a Docker image
that wraps `signal-cli` in an HTTP/WebSocket API.  **No official Signal API exists**; this is an
unofficial protocol implementation.

1. Run the Docker image (example):
   ```bash
   docker run -d -p 8080:8080 \
     -v /opt/signal-cli-config:/home/user/.local/share/signal-cli \
     bbernhard/signal-cli-rest-api
   ```
2. Register or link the number with signal-cli (see the image docs):
   - New number: `POST http://localhost:8080/v1/register/<number>`
   - Verify: `POST http://localhost:8080/v1/register/<number>/verify/<code>`
   - Or link an existing Signal account via QR code with `signal-cli link`.
3. Set `SIGNAL_API_URL=http://localhost:8080` and `SIGNAL_NUMBER=+15551234567`.
4. Add `signal` to `ENABLED_PLATFORMS`.

The adapter receives messages over the WebSocket endpoint
`ws://localhost:8080/v1/receive/<number>` and reconnects automatically on drop.

Outbox recipient: E.164 phone number, e.g. `+15559876543`.

**Signal caveats:** signal-cli uses the unofficial Signal protocol.  Signal Inc. may change the
protocol without notice.  Use a dedicated number and keep volumes reasonable.

### iMessage

Uses [BlueBubbles Server](https://bluebubbles.app) running on a Mac.  **iMessage is a closed,
Mac-only protocol**; there is no first-party cross-platform API.  BlueBubbles is the only viable
unofficial interface.

1. Install BlueBubbles Server on a Mac with iMessage already configured.
2. In BlueBubbles Server → Settings → Webhooks, add your gateway's webhook URL:
   `http://<gateway-host>:<IMESSAGE_WEBHOOK_PORT>/webhook` (port default: 3001).
   Select **New Message** as the event type.
3. Set `BLUEBUBBLES_URL=http://mac.local:1234` (or the Mac's IP/mDNS hostname + port).
4. Set `BLUEBUBBLES_PASSWORD` to the password shown in BlueBubbles Server → Settings → General.
5. Optionally set `IMESSAGE_WEBHOOK_PORT` (default `3001`) if you need a different port.
6. Add `imessage` to `ENABLED_PLATFORMS`.

The adapter exposes a tiny HTTP webhook listener (`node:http`) on `IMESSAGE_WEBHOOK_PORT`.
BlueBubbles POSTs new-message events to it.

Outbox recipient: a chat GUID (`iMessage;-;+15551234567`) or a bare E.164 handle.  BlueBubbles
accepts both shapes; the adapter detects which format is being used by the presence of a
semicolon.

**iMessage caveats:**
- Requires a Mac running continuously.
- BlueBubbles is unofficial and depends on undocumented macOS APIs; it may break on macOS updates.
- The Mac must stay awake and have iMessage signed in.
- The gateway host must be network-reachable from the Mac (or be the Mac itself).

---

## Easy setup (QR web page)

When the gateway is running it serves a local web page at
`http://localhost:8088` (or `http://localhost:$SETUP_PORT` if you changed the
default).  Open that URL in a browser on the machine running the gateway.

**WhatsApp** — the page renders the current QR code as a scannable image.
Open WhatsApp on your phone → **Settings → Linked Devices → Link a Device**,
then scan the QR shown in the browser.  The page flips to a Connected status
once the link is complete.  No more hunting through `docker logs` output.

**Token-based platforms (Slack, Discord, Signal)** — the page shows a
per-platform config checklist confirming which env vars are set and whether the
adapter connected successfully.

**Security:** this page can link your personal WhatsApp account.  The compose
file binds the port to `127.0.0.1` only so it is not reachable from outside the
host.  Do not expose `SETUP_PORT` via Caddy or any public proxy.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BACKEND_URL` | yes | — | Base URL of the ORC backend, e.g. `http://localhost:8000` |
| `MESSAGING_GATEWAY_TOKEN` | yes | — | Bearer token — must match `MESSAGING_GATEWAY_TOKEN` configured in the backend |
| `ENABLED_PLATFORMS` | no | `whatsapp,slack,discord` | Comma-separated list of platforms to activate |
| `POLL_INTERVAL_MS` | no | `5000` | How often (ms) to poll each platform's outbox |
| `SLACK_BOT_TOKEN` | if slack enabled | — | `xoxb-…` Bot User OAuth Token |
| `SLACK_APP_TOKEN` | if slack enabled | — | `xapp-…` App-Level Token (Socket Mode) |
| `DISCORD_TOKEN` | if discord enabled | — | Discord bot token |
| `SIGNAL_API_URL` | if signal enabled | — | Base URL of the signal-cli-rest-api instance, e.g. `http://localhost:8080` |
| `SIGNAL_NUMBER` | if signal enabled | — | Registered/linked E.164 number, e.g. `+15551234567` |
| `BLUEBUBBLES_URL` | if imessage enabled | — | Base URL of BlueBubbles Server on the Mac, e.g. `http://mac.local:1234` |
| `BLUEBUBBLES_PASSWORD` | if imessage enabled | — | BlueBubbles Server password |
| `IMESSAGE_WEBHOOK_PORT` | no | `3001` | Local port for the BlueBubbles incoming webhook listener |

---

## How it connects to the backend

The gateway authenticates to the backend with an `Authorization: Bearer <MESSAGING_GATEWAY_TOKEN>`
header on every request. The value must match what the backend expects — configure it identically
on both sides.

Backend API contract:

```
GET  {BACKEND_URL}/api/gateway/outbox?platform=<p>&max=20
     -> { messages: [{ id, platform, recipient, text }] }

POST {BACKEND_URL}/api/gateway/inbound
     { platform, chat_id, sender, text }
     -> { reply: string | null }
```

---

## Running

```bash
# Install
npm install

# Set required env vars (or use a .env loader)
export BACKEND_URL=http://localhost:8000
export MESSAGING_GATEWAY_TOKEN=changeme
export ENABLED_PLATFORMS=slack,discord   # omit whatsapp if no personal account to use

# Start (dev, with auto-recompile via tsx)
npm start

# Production
npm run build          # compiles to dist/
node dist/index.js

# Type check
npm run typecheck

# Unit tests
npm test
```

---

## Attribution

Library choices (same as [pi-messenger-bridge](https://github.com/tintinweb/pi-messenger-bridge),
MIT licence):
- WhatsApp: [@whiskeysockets/baileys](https://github.com/WhiskeySockets/Baileys)
- Slack: [@slack/bolt](https://github.com/slackapi/bolt-js)
- Discord: [discord.js](https://discord.js.org/)
- QR rendering: [qrcode-terminal](https://github.com/gtanner/qrcode-terminal)
- Signal: [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) (unofficial Signal protocol, wraps [signal-cli](https://github.com/AsamK/signal-cli))
- iMessage: [BlueBubbles Server](https://bluebubbles.app) (unofficial macOS API, Mac-only)
