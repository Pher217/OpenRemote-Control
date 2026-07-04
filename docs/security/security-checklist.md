# Pre-Deploy Security Checklist

Derived from [threat-model.md](threat-model.md). Run through this before
exposing a backend to real Telegram traffic, and again after any credential
rotation.

## Identity gate

- [ ] `TELEGRAM_ALLOWED_CHAT_IDS` is set to the exact Telegram user ids that
      should be able to drive sessions — nothing broader. Unset means
      deny-all (`backend/config/settings/base.py`); confirm that is not
      accidentally masking a config you meant to set.
- [ ] `TELEGRAM_FORUM_CHAT_ID` points at the forum group you actually created
      for this deployment, not a placeholder or a previous project's group.
- [ ] You understand the gate is **identity-based, not per-action** — see
      "Trust Model" in [SECURITY.md](../../SECURITY.md). If you need a
      per-tool Allow/Deny gate, set `ORC_HEADLESS_ENGINE=sdk` instead of the
      default.

## Credentials

- [ ] `TELEGRAM_BOT_TOKEN` is a **dedicated** bot token created for this
      deployment — not reused from another bot/service.
- [ ] `.env` (backend) and `deploy/orc-stack/orc-stack.env` (host) are not
      committed — both are covered by `.gitignore`; double-check with
      `git check-ignore -v backend/.env deploy/orc-stack/orc-stack.env`.
- [ ] `POSTGRES_PASSWORD` and `SECRET_KEY` are real values, not the throwaway
      `acc_user`/`acc_password` dev defaults from the README's dev-loop
      snippet.
- [ ] Host enrollment tokens are per-host (`orc-host enroll` issues one per
      machine) — never share a token across hosts.

## Rotation (on any suspicion of compromise)

- [ ] Rotate the Telegram bot token via BotFather, update `TELEGRAM_BOT_TOKEN`,
      redeploy, and confirm the old token is dead (a call with it should
      fail).
- [ ] Re-run `orc-host enroll` for the affected host — `HostToken.issue`
      automatically revokes the host's prior active token when a new one is
      issued (`backend/apps/hostlink/models.py`).
- [ ] Rotate `POSTGRES_PASSWORD` / `SECRET_KEY` if there is any reason to
      believe the backend `.env` was exposed.
- [ ] Review the audit log (Postgres, append-only) for activity in the
      suspected compromise window before rotating, so you don't lose the
      forensic trail.

## Process hygiene

- [ ] Exactly **one** Telegram bot process (`manage.py run_telegram_bot`) is
      running — two consumers steal each other's `getUpdates`, causing
      dropped or duplicated inbound messages. Check with:
      `pgrep -f 'manage.py run_telegram_bot' | wc -l` (must print `1`), or
      run `deploy/orc-stack/status.sh`, which does this for you.
- [ ] If running under the `orc-stack` launchd supervisor
      (`deploy/orc-stack/install.sh`), logs land under
      `~/Library/Logs/orc-stack/{daphne,bot,daemon}.log` (or the `ORC_LOGS`
      you configured) — confirm they exist and are being written to before
      relying on the supervisor for incident response.
- [ ] `deploy/orc-stack/status.sh` reports the backend healthy
      (`HTTP 401/503` on the probed endpoint means the service is up and the
      auth gate is working — not a broken deploy).

## Scope reminder

- [ ] You are deploying **single-operator, self-hosted** — not multi-tenant.
      There is no per-connector "drive" scope; see SECURITY.md's Scope
      section for what is and isn't covered by the security policy.
