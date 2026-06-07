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
│  - app.example.com      → web:8000           │
│  - matrix.example.com   → synapse:8008       │
│  - headscale.example.com→ headscale:8080     │
│  - example.com/.well-known/matrix/*          │
└────────┬────────────────┬────────────────────┘
         │                │
    orc_app network   orc_matrix network
         │                │
┌────────┴──────┐  ┌──────┴──────────────────────┐
│  Django / DRF │  │  Synapse v1.154.0            │
│  Gunicorn +   │  │  mautrix-whatsapp v26.05     │
│  Uvicorn      │  │  mautrix-slack    v26.05     │
│  Celery worker│  │  matrix-postgres             │
│  Telegram bot │  └─────────────────────────────┘
│  Matrix bot   │
│  Session obs. │  ┌─────────────────────────────┐
│  postgres:16  │  │  Headscale 0.24.2            │
│  valkey:8     │  │  (orc_headscale network)     │
└───────────────┘  └─────────────────────────────┘

2nd machine (developer workstation / CI runner)
  ├── orc-host daemon  (host-agent)
  └── orc-mcp bridge  (Cursor / Claude Code / etc.)
```

**Stacks** (each is an independent `docker compose` project):

| Stack | Compose file | Network(s) |
|---|---|---|
| App | `deploy/app/docker-compose.yml` | `orc_app` |
| Caddy | `deploy/caddy/docker-compose.yml` | joins `orc_app` + `orc_matrix` |
| Matrix | `deploy/matrix/docker-compose.yml` | `orc_matrix` |
| Headscale | `deploy/headscale/docker-compose.yml` | `orc_headscale` |

---

## 1. Prerequisites

**On the server:**

```
[ ] Docker >= 26  (includes Compose v2 — use `docker compose`, not `docker-compose`)
[ ] curl, openssl  (usually pre-installed)
[ ] Ports 80 and 443 open inbound in firewall / security group
[ ] Tailscale or direct SSH access for ongoing management
```

**DNS** — three A records pointing to your server's public IP:

```
app.example.com        A  <server-ip>
matrix.example.com     A  <server-ip>
headscale.example.com  A  <server-ip>
```

If you want Matrix federation (other servers can reach yours), also add:

```
example.com            A  <server-ip>   (for /.well-known/matrix/*)
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
APP_DOMAIN / MATRIX_DOMAIN / HS_DOMAIN / BASE_DOMAIN
POSTGRES_PASSWORD
MATRIX_POSTGRES_PASSWORD
ORC_CONNECTOR_TOKEN   # python -c "import secrets; print(secrets.token_hex(32))"
ORC_ENROLL_SECRET     # python -c "import secrets; print(secrets.token_hex(32))"
TELEGRAM_BOT_TOKEN    # from @BotFather
TELEGRAM_ALLOWED_CHAT_IDS
ORC_PROMPT_CHAT_ID
```

Matrix / headscale values can be filled in later (steps 5 and 6); leave them as
placeholders for now, or the matrix-bot will fail to connect (that's fine for
the initial app bring-up).

---

## 3. Bring Up the App Stack

### 3a. Build the Django image

```bash
docker compose -f deploy/app/docker-compose.yml build
```

This runs `collectstatic` at build time using the `build-placeholder` SECRET_KEY.
Static files are baked into the image — no volume needed.

### 3b. Run migrations

```bash
docker compose -f deploy/app/docker-compose.yml run --rm migrate
```

The `migrate` service runs `python manage.py migrate --noinput` against
`postgres` (started automatically as a dependency) and exits.

### 3c. Start all app services

```bash
docker compose -f deploy/app/docker-compose.yml up -d
```

Services started: `postgres`, `valkey`, `web`, `worker`, `telegram-bot`,
`matrix-bot`, `session-observer`.

`matrix-bot` will log connection errors until you finish step 5 — that's normal.

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

# Logs
docker compose -f deploy/app/docker-compose.yml logs -f web
```

---

## 4. Bring Up Caddy (TLS)

Caddy reads `APP_DOMAIN`, `MATRIX_DOMAIN`, `HS_DOMAIN`, and `BASE_DOMAIN` from
the `.env` file at startup.

**Caddy must join both `orc_app` and `orc_matrix` networks.** Both networks must
already exist (they are created by the app and matrix stacks). Start Caddy after
the app stack is up; it can be started before or after the matrix stack.

```bash
docker compose -f deploy/caddy/docker-compose.yml up -d
```

Caddy will obtain TLS certificates from Let's Encrypt on first startup. Watch
the logs to confirm:

```bash
docker compose -f deploy/caddy/docker-compose.yml logs -f caddy
# Look for: "certificate obtained successfully"
```

Verify HTTPS:

```bash
curl -s https://<APP_DOMAIN>/health/
# Expected: HTTP 200
```

---

## 5. Matrix Homeserver + Bridges

> **Order is critical.** Follow each step before moving to the next.

### 5a. Generate Synapse config

```bash
# Create the data directory that Synapse expects
mkdir -p deploy/matrix/synapse/data/appservices

# Generate homeserver.yaml (runs generate mode, writes to the mounted volume)
docker run --rm \
  -v "$(pwd)/deploy/matrix/synapse/data:/data" \
  -e SYNAPSE_SERVER_NAME=<MATRIX_DOMAIN> \
  -e SYNAPSE_REPORT_STATS=no \
  matrixdotorg/synapse:v1.154.0 generate
```

### 5b. Configure homeserver.yaml

```bash
$EDITOR deploy/matrix/synapse/data/homeserver.yaml
```

Apply these changes (use `deploy/matrix/synapse/homeserver.sample.yaml` as reference):

1. Change the `database:` block from SQLite to PostgreSQL:
   ```yaml
   database:
     name: psycopg2
     txn_limit: 10000
     args:
       user: synapse
       password: "<MATRIX_POSTGRES_PASSWORD>"   # from .env
       database: synapse
       host: matrix-postgres
       port: 5432
       cp_min: 5
       cp_max: 10
   ```

2. Set `enable_registration: false`

3. Add a `registration_shared_secret`:
   ```bash
   # Generate one:
   openssl rand -hex 32
   ```
   ```yaml
   registration_shared_secret: "<the-hex-string>"
   ```

4. Add the appservices list (bridges will write their files here):
   ```yaml
   app_service_config_files:
     - /data/appservices/whatsapp-registration.yaml
     - /data/appservices/slack-registration.yaml
   ```

5. Ensure `x_forwarded: true` under the listener (already set by `generate`
   in recent Synapse versions — confirm it is present).

### 5c. Start Synapse + matrix-postgres

```bash
docker compose -f deploy/matrix/docker-compose.yml up -d matrix-postgres synapse
```

Wait for Synapse to be healthy:

```bash
docker compose -f deploy/matrix/docker-compose.yml logs -f synapse
# Look for: "Synapse now listening on ..."
```

### 5d. Register the ORC bot user + your operator account

Replace `<REGISTRATION_SHARED_SECRET>` with the value you set in step 5b.

```bash
# Register the ORC bot (the account matrix-bot logs in as)
docker compose -f deploy/matrix/docker-compose.yml exec synapse \
  register_new_matrix_user \
  -u orc \
  -p "<strong-password>" \
  -a \
  -c /data/homeserver.yaml \
  http://localhost:8008

# Register yourself as operator
docker compose -f deploy/matrix/docker-compose.yml exec synapse \
  register_new_matrix_user \
  -u <your-username> \
  -p "<strong-password>" \
  -a \
  -c /data/homeserver.yaml \
  http://localhost:8008
```

### 5e. Get the ORC bot's Matrix access token

```bash
curl -s -X POST \
  "https://<MATRIX_DOMAIN>/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "m.login.password",
    "identifier": {"type": "m.id.user", "user": "orc"},
    "password": "<strong-password>"
  }' | python3 -m json.tool
```

Copy the `access_token` value from the response and set it in `deploy/.env`:

```bash
MATRIX_ACCESS_TOKEN=<the-token>
```

### 5f. Create a room and get its ID

Use a Matrix client (Element, Cinny, etc.) logged in as your operator account,
or use the API:

```bash
curl -s -X POST \
  "https://<MATRIX_DOMAIN>/_matrix/client/v3/createRoom" \
  -H "Authorization: Bearer <OPERATOR_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ORC Control",
    "preset": "private_chat",
    "invite": ["@orc:<MATRIX_DOMAIN>"]
  }' | python3 -m json.tool
```

Copy the `room_id` (format: `!abc123:<MATRIX_DOMAIN>`) and set in `deploy/.env`:

```bash
MATRIX_ALLOWED_ROOMS=!<room-id>:<MATRIX_DOMAIN>
ORC_PROMPT_MATRIX_ROOM=!<room-id>:<MATRIX_DOMAIN>
MATRIX_APPROVED_MXIDS=@<your-username>:<MATRIX_DOMAIN>
```

### 5g. Generate bridge configs and registration files

**WhatsApp bridge:**

```bash
# Copy sample config to the data directory
cp deploy/matrix/whatsapp/config.sample.yaml deploy/matrix/whatsapp/config.yaml
$EDITOR deploy/matrix/whatsapp/config.yaml
# Set: homeserver.domain, appservice.address, bridge.permissions operator MXID

# Generate registration.yaml (one-shot run)
docker compose -f deploy/matrix/docker-compose.yml run --rm whatsapp-bridge
```

**Slack bridge:**

```bash
cp deploy/matrix/slack/config.sample.yaml deploy/matrix/slack/config.yaml
$EDITOR deploy/matrix/slack/config.yaml
# Set: homeserver.domain, appservice.address, bridge.permissions operator MXID

docker compose -f deploy/matrix/docker-compose.yml run --rm slack-bridge
```

### 5h. Wire registration files into Synapse

```bash
cp deploy/matrix/whatsapp/registration.yaml \
   deploy/matrix/synapse/data/appservices/whatsapp-registration.yaml

cp deploy/matrix/slack/registration.yaml \
   deploy/matrix/synapse/data/appservices/slack-registration.yaml
```

Restart Synapse to pick up the appservice registrations:

```bash
docker compose -f deploy/matrix/docker-compose.yml restart synapse
```

Verify Synapse loaded both:

```bash
docker compose -f deploy/matrix/docker-compose.yml logs synapse | grep -i appservice
# Expected: "Loaded 2 appservices"
```

### 5i. Start the bridges

```bash
docker compose -f deploy/matrix/docker-compose.yml up -d whatsapp-bridge slack-bridge
```

### 5j. Link WhatsApp (QR login)

> mautrix-whatsapp uses your **personal WhatsApp account** via the multi-device
> QR code — not the WhatsApp Cloud API. You are scanning the same QR code you'd
> scan in WhatsApp Web. One phone, one active session.

In your Matrix client (logged in as your operator account), open a DM with
`@whatsappbot:<MATRIX_DOMAIN>` and send:

```
!wa login
```

The bridge will reply with a QR code image. Scan it with WhatsApp on your phone
(Settings → Linked Devices → Link a Device). Once linked, confirm in the DM.

Enable relay mode in the groups you want bridged:

```
!wa set-relay
```

This makes the bridge forward messages from non-linked Matrix users into
WhatsApp using the relay format defined in `config.yaml`.

### 5k. Link Slack

In a DM with `@slackbot:<MATRIX_DOMAIN>`:

```
!slack login
```

Follow the OAuth flow that the bridge presents. After linking, use
`!slack set-relay` in any bridged room.

### 5l. Restart matrix-bot with final .env

```bash
docker compose -f deploy/app/docker-compose.yml restart matrix-bot
```

Verify it connects:

```bash
docker compose -f deploy/app/docker-compose.yml logs -f matrix-bot
# Expected: no connection errors, bot joins the room
```

---

## 6. Headscale + Join the 2nd Machine

Headscale needs the Caddy stack running first so `HS_DOMAIN` is accessible over
HTTPS (headscale clients need TLS).

### 6a. Start Headscale

```bash
# Edit the server_url in config.yaml to match your HS_DOMAIN
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
# Create a user (logical grouping for your nodes)
docker compose -f deploy/headscale/docker-compose.yml exec headscale \
  headscale users create orc

# Create a reusable pre-auth key (expires in 24h; use --ephemeral for one-time)
docker compose -f deploy/headscale/docker-compose.yml exec headscale \
  headscale preauthkeys create --user orc --reusable --expiration 24h
```

Copy the key. You'll use it on every machine that joins the mesh.

### 6c. Join the 2nd machine (developer workstation / CI runner)

On the 2nd machine, install Tailscale (standard package):

```bash
# Linux
curl -fsSL https://tailscale.com/install.sh | sh

# macOS
brew install tailscale
```

Point it at your Headscale server:

```bash
sudo tailscale up \
  --login-server https://<HS_DOMAIN> \
  --authkey <PREAUTHKEY> \
  --hostname dev-workstation
```

The server's backend will be reachable inside the mesh as
`web.<user>.mesh.internal` (or by its Tailscale IP).

---

## 7. Install + Run the Host-Agent Daemon (2nd Machine)

The host-agent daemon ships in `host-agent/` in this repo. It registers the
machine as a controllable host and tails live session output.

```bash
# Install (pipx keeps it isolated)
pipx install ./host-agent

# Enroll this machine with the backend
# Use the mesh address or direct address of the backend
orc-host enroll \
  --backend http://web.<user>.mesh.internal:8000 \
  --secret <ORC_ENROLL_SECRET>

# Start the daemon (runs in the foreground; use a service manager or tmux)
orc-host daemon --runtimes claude_code,codex
```

`--runtimes` is a comma-separated list matching `OBSERVE_RUNTIMES` in `.env`.

To run as a systemd service:

```bash
# Adjust paths as needed
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

The MCP bridge (`connectors/orc-mcp`) exposes ORC as an MCP server that
Cursor, Claude Code, or any MCP-compatible IDE can connect to.

```bash
pipx install ./connectors/orc-mcp
```

Set environment variables (add to shell profile or pass per-command):

```bash
export ORC_BACKEND_URL=https://<APP_DOMAIN>
export ORC_CONNECTOR_TOKEN=<ORC_CONNECTOR_TOKEN>   # from deploy/.env
```

**Claude Code (claude CLI):**

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

Restart Cursor after saving. Verify the MCP server appears in the Cursor MCP
panel (Settings → MCP).

---

## 9. End-to-End Verification

### 9a. Telegram → Approval prompt

1. Send a message to your Telegram bot from an allowed chat ID.
2. Confirm the bot responds.
3. In Claude Code (with MCP bridge active), invoke a tool that triggers
   `ask_human`. A prompt should appear in the Telegram chat.
4. Reply in Telegram. Confirm the tool call resumes.

### 9b. Matrix → Approval prompt

1. In your Matrix client, send a message in the ORC Control room.
2. Confirm the ORC bot (`@orc`) responds.
3. Trigger an `ask_human` from a tool call.
4. Confirm the prompt appears in the room and the response is forwarded back.

### 9c. Session observer

On the 2nd machine (where orc-host is running), start a Claude Code session.
In the Django admin or via the API, confirm a new session entry appears with
live output tailing.

---

## Troubleshooting

**`migrate` service exits with error:**
- Check postgres is healthy: `docker compose -f deploy/app/docker-compose.yml ps`
- Check logs: `docker compose -f deploy/app/docker-compose.yml logs postgres`

**`web` healthcheck fails:**
- `/health/` endpoint not yet merged — ensure you're on a branch where Opus has
  added it. Temporarily change the healthcheck to `CMD true` to confirm the
  rest of the stack works.

**Caddy: `certificate obtained: false` / ACME errors:**
- DNS must propagate before Caddy can get a cert. Wait and retry.
- Check ports 80/443 are open: `curl http://<server-ip>` from an external machine.

**Synapse: "Failed to load appservice" on startup:**
- Confirm the registration YAML files are in `deploy/matrix/synapse/data/appservices/`.
- Check the paths in `homeserver.yaml` match the container-internal path `/data/appservices/...`.

**Matrix bot: login errors:**
- Double-check `MATRIX_ACCESS_TOKEN` in `.env` — token is per-device and
  invalidated on logout.
- Re-run the login `curl` in step 5e and update `.env`.

**WhatsApp bridge: QR code expires:**
- Send `!wa logout` then `!wa login` again in the DM.

**Headscale: machine not appearing after `tailscale up`:**
- Confirm the pre-auth key is still valid: `headscale preauthkeys list --user orc`
- Check headscale logs: `docker compose -f deploy/headscale/docker-compose.yml logs`

---

## Security Notes

- **Single shared token:** `ORC_CONNECTOR_TOKEN` is a shared secret. Rotate it
  by updating `.env` and restarting `web`, `worker`, and all client installs.
- **Bridge puppets are untrusted.** Never add the WhatsApp or Slack bridge puppet
  MXIDs (e.g. `@whatsapp_+1234:matrix.example.com`) to `MATRIX_APPROVED_MXIDS`.
  Those are relay proxies, not verified operators.
- **WhatsApp = personal account via QR.** The bridge uses your phone's WhatsApp
  session. If your phone is offline or the session is invalidated, the bridge
  drops. This is inherent to the multi-device protocol — it is not the
  WhatsApp Business Cloud API.
- **`SECURE_PROXY_SSL_HEADER`** must be set so Django enforces HTTPS-only
  cookies behind Caddy. Without it, `SESSION_COOKIE_SECURE` has no effect.
- **Postgres passwords:** use `openssl rand -hex 24` for each.
- **No federation by default:** `enable_registration: false` and no public
  federation. The Matrix homeserver is private.
- **Headscale ACL:** `policy.hujson` is allow-all. Restrict it once your node
  list is stable.
