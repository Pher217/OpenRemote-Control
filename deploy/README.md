# OpenRemote-Control — Production Deploy Runbook

> Single-operator guide. Follow sections in order on a fresh Linux server.
> All commands are copy-paste-ready. Replace placeholder values (shown as
> `<angle-brackets>` or `example.com` variants) with your real values.

---

## 0. What You Get

```
Internet
   │  443 / 80
   ▼
┌──────────────────────────────────────────────┐
│  Caddy 2  (TLS termination, reverse proxy)   │
│  - app.example.com       → web:8000          │
│  - headscale.example.com → headscale:8080    │
└────────┬─────────────────────────────────────┘
         │
    orc_app network
         │
┌────────┴─────────────────────────────────────┐
│  Django / DRF (Gunicorn + Uvicorn)           │
│  Celery worker                               │
│  Telegram bot         (run_telegram_bot)     │
│  Session observer     (run_session_observer) │
│  messaging-gateway    (Node.js sidecar)      │
│    └─ WhatsApp (Baileys, unofficial — QR)    │
│    └─ Slack    (Socket Mode bot)             │
│    └─ Discord  (discord.js bot)              │
│    └─ Signal   (via signal-cli-rest-api)     │
│    └─ iMessage (via BlueBubbles on Mac)      │
│  signal-cli-rest-api                         │
│  postgres:16                                 │
│  valkey:8                                    │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│  Headscale 0.24.2  (orc_headscale network)   │
└──────────────────────────────────────────────┘

2nd machine (developer workstation / CI runner)
  ├── orc-host daemon  (host-agent)
  └── orc-mcp bridge  (Cursor / Claude Code / etc.)

Mac (separate machine — not a container)
  └── BlueBubbles  (iMessage relay — optional)
```

**Stacks** (each is an independent `docker compose` project):

| Stack | Compose file | Network(s) |
|---|---|---|
| App + gateway | `deploy/app/docker-compose.yml` | `orc_app` |
| Caddy | `deploy/caddy/docker-compose.yml` | joins `orc_app` + `orc_headscale` |
| Headscale | `deploy/headscale/docker-compose.yml` | `orc_headscale` |

> **Telegram** is handled by the backend's own `run_telegram_bot` management
> command and does not need the gateway.  The gateway handles WhatsApp, Slack,
> Discord, Signal, and iMessage.

---

## 1. Prerequisites

**On the server:**

```
[ ] Docker >= 26  (includes Compose v2 — use `docker compose`, not `docker-compose`)
[ ] curl, openssl  (usually pre-installed)
[ ] Ports 80 and 443 open inbound in firewall / security group
[ ] Tailscale or direct SSH access for ongoing management
```

**DNS** — two A records pointing to your server's public IP:

```
app.example.com        A  <server-ip>
headscale.example.com  A  <server-ip>
```

---

## 2. Configure `.env`

```bash
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
```

Minimum values to set before proceeding:

```
SECRET_KEY            # python -c "import secrets; print(secrets.token_urlsafe(50))"
APP_DOMAIN / HS_DOMAIN
POSTGRES_PASSWORD
ORC_CONNECTOR_TOKEN   # python -c "import secrets; print(secrets.token_hex(32))"
ORC_ENROLL_SECRET     # python -c "import secrets; print(secrets.token_hex(32))"
MESSAGING_GATEWAY_TOKEN  # python -c "import secrets; print(secrets.token_hex(32))"
TELEGRAM_BOT_TOKEN    # from @BotFather
TELEGRAM_ALLOWED_CHAT_IDS
ORC_PROMPT_CHAT_ID
ENABLED_PLATFORMS     # e.g. whatsapp,slack,signal
```

Messaging-platform tokens (Slack, Discord, Signal, iMessage) can be filled in
after the stack is up; the gateway skips any platform not in `ENABLED_PLATFORMS`.

---

## 3. Bring Up the App Stack

### 3a. Build the Django + gateway images

```bash
docker compose -f deploy/app/docker-compose.yml build
```

This compiles the Django image (collectstatic at build time, baked in) and the
`orc-messaging-gateway` Node.js image.

### 3b. Run migrations

```bash
docker compose -f deploy/app/docker-compose.yml run --rm migrate
```

The `migrate` service runs `python manage.py migrate --noinput` and exits.

### 3c. Start all app services

```bash
docker compose -f deploy/app/docker-compose.yml up -d
```

Services started: `postgres`, `valkey`, `web`, `worker`, `telegram-bot`,
`session-observer`, `messaging-gateway`, `signal-cli-rest-api`.

### 3d. Create a Django superuser

```bash
docker compose -f deploy/app/docker-compose.yml \
  run --rm web python manage.py createsuperuser
```

### 3e. Verify

```bash
# Health endpoint (from the server itself — bypasses Caddy)
curl -s http://localhost:8000/health/
# Expected: HTTP 200

# Gateway logs
docker compose -f deploy/app/docker-compose.yml logs -f messaging-gateway
```

---

## 4. Bring Up Caddy (TLS)

Caddy reads `APP_DOMAIN` and `HS_DOMAIN` from the `.env` file at startup.

**Caddy must join `orc_app` and `orc_headscale`.** Both networks must already
exist (created by the app and headscale stacks). Start Caddy after step 3.

```bash
docker compose -f deploy/caddy/docker-compose.yml up -d
```

Verify TLS:

```bash
docker compose -f deploy/caddy/docker-compose.yml logs -f caddy
# Look for: "certificate obtained successfully"

curl -s https://<APP_DOMAIN>/health/
# Expected: HTTP 200
```

---

## 5. Messaging Gateway — Per-Platform Setup

The gateway is a Node.js sidecar that polls the backend's `/api/gateway/outbox`
and forwards outbound messages to each enabled platform, then POSTs inbound
messages to `/api/gateway/inbound`.  It authenticates with `MESSAGING_GATEWAY_TOKEN`.

Only platforms listed in `ENABLED_PLATFORMS` are started; the gateway logs a
skip message for disabled platforms.

### 5a. Single messaging app of choice

ORC routes every session notification and approval prompt to **one** messaging
app — not all of them at once.  Set `ORC_MESSAGING_PLATFORM` in `.env` to the
app you want:

```
ORC_MESSAGING_PLATFORM=telegram   # or: whatsapp | slack | signal | imessage | discord
```

Then configure **only** that platform's recipient variable:

| Platform   | Recipient variable        | Format |
|------------|--------------------------|--------|
| `telegram` | `ORC_PROMPT_CHAT_ID`      | Telegram chat ID |
| `whatsapp` | `ORC_PROMPT_WHATSAPP`     | International phone number, e.g. `+41791234567` |
| `slack`    | `ORC_PROMPT_SLACK`        | Channel ID or user ID |
| `signal`   | `ORC_PROMPT_SIGNAL`       | International phone number |
| `imessage` | `ORC_PROMPT_IMESSAGE`     | Apple ID email or phone number |
| `discord`  | `ORC_PROMPT_DISCORD`      | Channel ID |

Telegram is delivered natively by the backend's `run_telegram_bot` command and
does not need the gateway sidecar.  For all other platforms, also add the
chosen platform to `ENABLED_PLATFORMS` so the gateway sidecar activates it.

**This is not a broadcast / multi-app fan-out.**  Only the platform named in
`ORC_MESSAGING_PLATFORM` receives messages — the others are idle even if their
tokens are present.

### 5b. Easy-setup web page

Once the gateway is running, open **http://localhost:8088** (or
`http://localhost:$SETUP_PORT`) in a browser **on the server** (or tunnel it
to your laptop with `ssh -L 8088:localhost:8088 <server>`).

The page lets you connect each platform without touching the terminal:

- **WhatsApp** — the QR code is rendered as a scannable image.  Scan it with
  **WhatsApp → Settings → Linked Devices → Link a Device**.  The page flips to
  Connected when the link succeeds — no more squinting at ASCII art in logs.
- **Token-based platforms (Slack, Discord, Signal)** — a config checklist
  shows which env vars are present and whether the adapter is live.

Previously the only option was:

```bash
docker compose -f deploy/app/docker-compose.yml logs -f messaging-gateway
```

…and scanning a small ASCII QR from a terminal window.  The web page is
strictly better for WhatsApp first-time setup.

**Security:** the compose file maps `SETUP_PORT` to `127.0.0.1` only — it is
not reachable from the internet.  Never add `SETUP_PORT` to your Caddy config
or expose it on a public interface.  Treat it as a local operator tool only.

### 5c. WhatsApp (Baileys — unofficial multi-device protocol)

> **Risk notice:** Baileys uses the WhatsApp Web multi-device protocol, which is
> not the official Cloud API.  Account bans are possible (rare in practice for
> low-volume personal use, but not zero).  Do not use a primary business number.

The gateway handles the QR scan automatically on first start.

1. Watch the gateway logs at startup:

   ```bash
   docker compose -f deploy/app/docker-compose.yml logs -f messaging-gateway
   ```

2. A QR code is printed in the logs as ASCII art.

3. On your phone: **WhatsApp → Settings → Linked Devices → Link a Device**.
   Scan the QR.

4. Auth state is persisted in the `messaging_gateway_data` Docker volume under
   `/app/data/whatsapp/`.  You will only need to re-scan if the session is
   revoked.

Set the prompt recipient in `.env`:

```
ORC_PROMPT_WHATSAPP=+41791234567   # international format
```

### 5d. Slack (Socket Mode bot)

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App →
   **From scratch**.

2. In **Socket Mode**, enable Socket Mode and generate an **App-Level Token**
   with scope `connections:write` → copy to `SLACK_APP_TOKEN`.

3. In **OAuth & Permissions**, add Bot Token Scopes:
   `chat:write`, `im:history`, `im:read`, `im:write`, `channels:history`

4. Install the app to your workspace → copy the **Bot User OAuth Token** to
   `SLACK_BOT_TOKEN`.

5. In **Event Subscriptions** → subscribe to bot events: `message.im`,
   `message.channels`.

6. Invite the bot to the channel or DM where prompts should appear.

Set the prompt recipient in `.env`:

```
ORC_PROMPT_SLACK=C01234ABCDE   # channel ID or U01234ABCDE user ID
```

Restart the gateway after updating `.env`:

```bash
docker compose -f deploy/app/docker-compose.yml restart messaging-gateway
```

### 5e. Discord (discord.js bot)

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
   → New Application → Bot.

2. Enable **Message Content Intent** under Privileged Gateway Intents.

3. Copy the **Bot Token** to `DISCORD_TOKEN`.

4. Invite the bot to your server with scopes `bot` and permission `Send Messages`.
   Invite URL format:
   `https://discord.com/api/oauth2/authorize?client_id=<CLIENT_ID>&permissions=2048&scope=bot`

5. Get the target channel ID: right-click the channel → **Copy Channel ID**
   (requires Developer Mode: User Settings → Advanced → Developer Mode).

Set the prompt recipient in `.env`:

```
ORC_PROMPT_DISCORD=1234567890123456789
```

### 5f. Signal (via signal-cli-rest-api)

The `signal-cli-rest-api` container ([bbernhard/signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api))
provides a JSON-RPC HTTP API around the Java Signal client.

**Register or link a number** (choose one):

**Option A — Register a new number** (requires an SMS or voice verification):

```bash
# Replace +41791234567 with the number you want to register
curl -X POST "http://localhost:8080/v1/register/+41791234567" \
  -H "Content-Type: application/json" \
  -d '{"use_voice": false}'

# Then verify with the SMS code you received
curl -X POST "http://localhost:8080/v1/register/+41791234567/verify/123456"
```

**Option B — Link an existing Signal account** (link as a secondary device):

```bash
# Get a linking URI
curl "http://localhost:8080/v1/qrcodelink?device_name=orc-server"
# Scan the QR code with your Signal mobile app
# (Settings → Linked Devices → Link New Device)
```

After registration/linking, the account data is persisted in the `signal_cli_data`
volume.

> The signal-cli-rest-api port (8080) is not published to the host — the gateway
> reaches it at `http://signal-cli-rest-api:8080` inside the `orc_app` network.
> Expose it temporarily for setup commands by adding `ports: ["127.0.0.1:8080:8080"]`
> to the `signal-cli-rest-api` service, then remove the port after setup.

Set the prompt recipient in `.env`:

```
SIGNAL_API_URL=http://signal-cli-rest-api:8080
SIGNAL_NUMBER=+41791234567   # the registered/linked number
ORC_PROMPT_SIGNAL=+41791234567
```

> **Risk notice:** Signal does not have an official third-party API.
> signal-cli uses the Signal protocol directly.  Account bans are rare for
> personal use but possible if traffic patterns are unusual.

### 5g. iMessage (via BlueBubbles — Mac only)

iMessage requires Apple hardware running macOS with the Messages app.
BlueBubbles ([bluebubbles.app](https://bluebubbles.app)) runs on that Mac and
exposes a REST API + webhook.  It does NOT run as a Docker container.

**On your Mac:**

1. Download and install BlueBubbles from [bluebubbles.app/install](https://bluebubbles.app/install/).

2. Open BlueBubbles → go through initial setup:
   - Set a server password → copy to `BLUEBUBBLES_PASSWORD`
   - Note the server URL (e.g. `http://192.168.1.x:1234`) → copy to `BLUEBUBBLES_URL`

3. In BlueBubbles → **Settings → Webhooks**, add a new webhook pointing at
   the gateway's webhook port on the server.  The Mac must reach the server
   via the Headscale mesh (or direct LAN):

   ```
   http://<server-tailscale-ip>:<IMESSAGE_WEBHOOK_PORT>
   ```

   The default `IMESSAGE_WEBHOOK_PORT` is 3001.

4. The gateway binds `IMESSAGE_WEBHOOK_PORT` on all interfaces inside its
   container.  Do NOT expose this port via Caddy — it should only be
   reachable from the Mac over the Headscale/Tailscale mesh or your LAN.

Set the prompt recipient in `.env`:

```
BLUEBUBBLES_URL=http://192.168.1.x:1234
BLUEBUBBLES_PASSWORD=change-me
IMESSAGE_WEBHOOK_PORT=3001
ORC_PROMPT_IMESSAGE=+41791234567
```

---

## 6. Headscale + Join the 2nd Machine

Headscale needs Caddy running first so `HS_DOMAIN` is accessible over HTTPS.

### 6a. Start Headscale

```bash
sed -i "s|https://headscale.example.com|https://<HS_DOMAIN>|g" \
  deploy/headscale/config.yaml

docker compose -f deploy/headscale/docker-compose.yml up -d
```

Verify:

```bash
docker compose -f deploy/headscale/docker-compose.yml logs -f headscale
curl -s https://<HS_DOMAIN>/health
# Expected: 200 OK
```

### 6b. Create a Headscale user and pre-auth key

```bash
docker compose -f deploy/headscale/docker-compose.yml exec headscale \
  headscale users create orc

docker compose -f deploy/headscale/docker-compose.yml exec headscale \
  headscale preauthkeys create --user orc --reusable --expiration 24h
```

Copy the key for step 6c.

### 6c. Join the 2nd machine

```bash
# Linux
curl -fsSL https://tailscale.com/install.sh | sh

# macOS
brew install tailscale
```

```bash
sudo tailscale up \
  --login-server https://<HS_DOMAIN> \
  --authkey <PREAUTHKEY> \
  --hostname dev-workstation
```

---

## 7. Install + Run the Host-Agent Daemon (2nd Machine)

```bash
pipx install ./host-agent

orc-host enroll \
  --backend http://web.<user>.mesh.internal:8000 \
  --secret <ORC_ENROLL_SECRET>

orc-host daemon --runtimes claude_code,codex
```

Systemd unit:

```bash
sudo tee /etc/systemd/system/orc-host.service <<'EOF'
[Unit]
Description=OpenRemote Control host-agent daemon
After=network-online.target

[Service]
ExecStart=/home/<user>/.local/bin/orc-host daemon --runtimes claude_code,codex
Environment=ORC_BACKEND_URL=http://web.<user>.mesh.internal:8000
Environment=ORC_ENROLL_SECRET=<ORC_ENROLL_SECRET>
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now orc-host
```

---

## 8. Install the Universal MCP Bridge (Client Machine)

```bash
pipx install ./connectors/orc-mcp
```

Set environment variables:

```bash
export ORC_BACKEND_URL=https://<APP_DOMAIN>
export ORC_CONNECTOR_TOKEN=<ORC_CONNECTOR_TOKEN>
```

**Claude Code:**

```bash
claude mcp add orc -- orc-mcp
```

**Cursor (`~/.cursor/mcp.json`):**

```json
{
  "mcpServers": {
    "orc": {
      "command": "orc-mcp",
      "env": {
        "ORC_BACKEND_URL": "https://<APP_DOMAIN>",
        "ORC_CONNECTOR_TOKEN": "<ORC_CONNECTOR_TOKEN>"
      }
    }
  }
}
```

---

## 9. End-to-End Verification

### 9a. Telegram → Approval prompt

1. Send a message to your Telegram bot from an allowed chat ID.
2. Confirm the bot responds.
3. In Claude Code (with MCP bridge active), invoke a tool that triggers
   `ask_human`. A prompt should appear in the Telegram chat.
4. Reply in Telegram. Confirm the tool call resumes.

### 9b. Gateway platform → Approval prompt

1. Confirm the gateway is running and the target platform is in `ENABLED_PLATFORMS`.

   ```bash
   docker compose -f deploy/app/docker-compose.yml logs messaging-gateway | head -30
   ```

2. Trigger an `ask_human` from a tool call.
3. Confirm the prompt appears on the configured platform (`ORC_PROMPT_*`) and
   the response is forwarded back to the backend.

### 9c. Session observer

On the 2nd machine (where orc-host is running), start a Claude Code session.
In the Django admin or via the API, confirm a new session entry appears with
live output tailing.

---

## Troubleshooting

**`migrate` service exits with error:**
- Check postgres: `docker compose -f deploy/app/docker-compose.yml ps`
- Check logs: `docker compose -f deploy/app/docker-compose.yml logs postgres`

**`web` healthcheck fails:**
- Temporarily change the healthcheck to `CMD true` to confirm the rest of the
  stack works, then investigate the `/health/` endpoint.

**Caddy: ACME / certificate errors:**
- DNS must propagate before Caddy can get a cert. Wait and retry.
- Check ports 80/443 are open from the outside.

**Gateway: WhatsApp QR not appearing:**
- Confirm `whatsapp` is in `ENABLED_PLATFORMS`.
- Check gateway logs: `docker compose -f deploy/app/docker-compose.yml logs -f messaging-gateway`
- Delete the auth state volume and restart to force a fresh QR:
  `docker volume rm orc_messaging_gateway_data`

**Gateway: Slack / Discord not connecting:**
- Verify tokens in `.env`. Restart after any `.env` change:
  `docker compose -f deploy/app/docker-compose.yml restart messaging-gateway`

**signal-cli-rest-api: account not registered:**
- Temporarily publish port 8080 for setup (see step 5d), run the register/link
  commands, then remove the port mapping and restart.

**Headscale: machine not appearing:**
- Confirm the pre-auth key is still valid:
  `headscale preauthkeys list --user orc`

---

## Security Notes

- **`MESSAGING_GATEWAY_TOKEN`** authenticates the gateway to the backend.
  Treat it like a password — rotate by updating `.env` and restarting both
  `web` and `messaging-gateway`.
- **`ORC_CONNECTOR_TOKEN`** is the shared secret for host-agents and MCP
  connectors. Rotate by updating `.env` and restarting `web`, `worker`, and
  all client installs.
- **WhatsApp / Signal use unofficial protocols.**  Both platforms can ban
  accounts for API-like usage.  Use dedicated numbers, keep volume low, and
  accept that sessions may require periodic re-linking.
- **iMessage webhook port** must NOT be exposed publicly.  Bind it only on
  the Tailscale/Headscale interface.  The gateway's `IMESSAGE_WEBHOOK_PORT`
  port in the compose file is intentionally not published to `0.0.0.0`.
- **BlueBubbles requires a Mac** you control with iMessage signed in.  It
  cannot run on a Linux server.
- **`SECURE_PROXY_SSL_HEADER`** must be set so Django enforces HTTPS-only
  cookies behind Caddy.
- **Postgres passwords:** use `openssl rand -hex 24` for each.
- **Headscale ACL:** `policy.hujson` is allow-all by default. Restrict it
  once your node list is stable.
