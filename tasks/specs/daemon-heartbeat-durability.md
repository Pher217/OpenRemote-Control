# Spec: Daemon heartbeat + watchdog durability fix

## Problem / root cause
The host daemon connects to the backend over a persistent WebSocket. The backend
`HostConsumer` does `group_add("host_<id>", channel_name)` on connect; the backend
delivers `pty.inject` (and other host commands) via `channel_layer.group_send`.

After the daemon has been connected for a while (and especially after Redis
reconnect churn), the backend consumer's Redis channel-receive **stalls silently**:
the WebSocket socket stays ESTABLISHED, daphne logs no disconnect, but
`group_send` messages are no longer delivered to the daemon. Result: a user's
Telegram keystrokes (`pty.inject`) are dispatched by the backend but **never reach
tmux**, with zero error anywhere. Restarting the daemon (fresh consumer + fresh
Redis receive) fixes it — proving the connection is "deaf but alive".

## Fix (two sides)
A daemon-driven **heartbeat** that the backend **echoes back through the group
path**, plus a daemon **watchdog** that forces a reconnect when the echo stops.
The echo deliberately travels backend-receive → `group_send` → Redis →
consumer.`host_command` → ws, i.e. the *exact* path that decays — so a stalled
consumer fails the heartbeat and the daemon reconnects (new consumer, fresh Redis
receive). Healthy connections are never disrupted (reconnect only on timeout).

Graceful degradation: if the backend does not answer heartbeats at all, the
watchdog simply forces a reconnect every `HEARTBEAT_TIMEOUT` seconds — still
fixing the decay, just less efficiently.

---

## Change 1 — Backend: `backend/apps/hostlink/consumers.py`

In `HostConsumer.receive_json`, add a branch for a new inbound message type
`"host_heartbeat"`. On receipt, group_send a `ping` host_command back to THIS
host's group (so it round-trips through the same Redis path that delivers
`pty.inject`). Preserve the optional `nonce` for observability.

Add this branch to the existing `if/elif` chain in `receive_json` (after the
existing `session.*` branches, before the final silent-ignore comment):

```python
        elif msg_type == "host_heartbeat":
            # Echo a ping back THROUGH the group path (group_send → Redis →
            # this consumer's host_command → ws). This exercises the exact
            # delivery path used by pty.inject, so the daemon's watchdog can
            # detect a silently-stalled channel receive and force a reconnect.
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "host_command", "command": "ping", "nonce": content.get("nonce", "")},
            )
```

Do NOT change the existing `host_command` handler (it already does
`await self.send_json(event)`, which forwards the ping down the ws).

---

## Change 2 — Daemon: `host-agent/agent_host/wsclient.py`

### 2a. Module-level constants
Near the top (after `MAX_EVENT_BYTES`), add:

```python
# Heartbeat: the daemon sends a host_heartbeat up to the backend every
# HEARTBEAT_INTERVAL seconds; the backend echoes a `ping` host_command back
# through the channel-layer group. If no ping returns within HEARTBEAT_TIMEOUT
# seconds, the channel path is presumed dead and the connection is torn down so
# the outer loop reconnects with a fresh backend consumer.
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 90.0
```

### 2b. Per-connection liveness state + heartbeat/watchdog coroutines
Inside `run_sender`, within the `async with connect(url) as ws:` block, alongside
the existing `_sender` / `_receiver` nested coroutines, do the following:

1. Just before defining `_sender`, add a monotonic liveness marker:
   ```python
                import time as _time  # noqa: PLC0415 — stdlib (module already imports time at top; this alias keeps the nested scope explicit)
                last_pong = _time.monotonic()
   ```
   (You may instead reuse the module-level `time` import — use `time.monotonic()`
   — and a `nonlocal`-friendly mutable holder. Simplest: use a single-element
   list `last_pong = [time.monotonic()]` so the nested coroutines can mutate
   `last_pong[0]` without `nonlocal` gymnastics. Pick the list-holder approach.)

   FINAL decision (implement exactly this): `last_pong = [time.monotonic()]`
   using the existing top-level `import time`.

2. In `_receiver`, when a frame is an inbound `host_command` whose `command` is
   `"ping"`, update liveness BEFORE dispatching to `on_command`:
   ```python
                        if frame.get("type") == "host_command":
                            if frame.get("command") == "ping":
                                last_pong[0] = time.monotonic()
                            try:
                                on_command(frame)
                            except Exception:
                                log.exception("on_command raised — ignoring")
   ```
   (Keep the existing `on_command(frame)` call and its try/except exactly; only
   add the two `if command == "ping"` lines updating `last_pong[0]`.)

3. Add a `_heartbeat` coroutine that periodically enqueues nothing and instead
   sends directly on the ws (so it does not depend on the offline queue):
   ```python
                async def _heartbeat() -> None:
                    while not stop.is_set():
                        await asyncio.sleep(HEARTBEAT_INTERVAL)
                        if stop.is_set():
                            return
                        try:
                            await ws.send(json.dumps({"type": "host_heartbeat", "nonce": uuid.uuid4().hex}))
                        except Exception:
                            # Send failed — propagate so gather tears down and reconnects.
                            raise
   ```

4. Add a `_watchdog` coroutine that forces a reconnect when the echo stops:
   ```python
                async def _watchdog() -> None:
                    while not stop.is_set():
                        await asyncio.sleep(HEARTBEAT_INTERVAL / 2)
                        if stop.is_set():
                            return
                        if time.monotonic() - last_pong[0] > HEARTBEAT_TIMEOUT:
                            log.warning(
                                "heartbeat timeout (%.0fs) — channel path presumed dead; reconnecting",
                                HEARTBEAT_TIMEOUT,
                            )
                            raise ConnectionError("heartbeat timeout")
   ```

5. Change the final gather from:
   ```python
                await asyncio.gather(_sender(), _receiver())
   ```
   to:
   ```python
                await asyncio.gather(_sender(), _receiver(), _heartbeat(), _watchdog())
   ```

Do not change the outer reconnect/backoff logic — a raised `ConnectionError`
propagates out of gather, exits the `async with`, and the existing outer loop
reconnects with a fresh signed URL.

---

## Tests

### Daemon: `host-agent/tests/` (match existing wsclient test style/fixtures)
Find the existing wsclient tests (e.g. `test_wsclient*.py`) and mirror their fake
ws / injectable `connect` + `stop` pattern. Add:

1. `test_heartbeat_sent_periodically`: with a fake ws and a short
   monkeypatched `HEARTBEAT_INTERVAL`, assert the daemon sends at least one
   `{"type":"host_heartbeat"}` frame within a bounded wait, then set `stop`.
2. `test_ping_resets_liveness_no_reconnect`: feed the receiver a
   `{"type":"host_command","command":"ping"}` frame periodically; assert the
   connection is NOT torn down (no reconnect) over a span > `HEARTBEAT_TIMEOUT`
   when `HEARTBEAT_TIMEOUT` is monkeypatched small (e.g. 0.3s) and pings arrive
   faster than that.
3. `test_watchdog_forces_reconnect_on_silence`: with `HEARTBEAT_TIMEOUT`
   monkeypatched small and NO ping frames returned, assert the fake `connect` is
   invoked more than once (i.e. a reconnect happened) within a bounded wait.

Keep tests deterministic and fast: monkeypatch the module constants to small
values (e.g. `HEARTBEAT_INTERVAL=0.05`, `HEARTBEAT_TIMEOUT=0.2`) via
`monkeypatch.setattr(wsclient, "HEARTBEAT_INTERVAL", 0.05)`. Use `asyncio` with a
hard overall timeout so a hang fails fast.

### Backend: `backend/apps/hostlink/tests/test_consumers.py` (or nearest)
Add a test that sending `{"type":"host_heartbeat","nonce":"abc"}` to the
consumer triggers a `group_send` of `{"type":"host_command","command":"ping",...}`
to `host_<id>`. Mock/patch `self.channel_layer.group_send` (or use the existing
in-memory channel layer the other consumer tests use) and assert it was called
with a ping host_command. Follow the exact pattern of existing consumer tests in
that file (they already exercise connect/auth + group messaging).

---

## Verification commands
```
# Daemon tests
cd host-agent && env -u VIRTUAL_ENV uv run --no-sync pytest tests/ -q -k "heartbeat or watchdog or ping"
# Backend tests
cd backend && env -u VIRTUAL_ENV DJANGO_SETTINGS_MODULE=config.settings.base uv run --no-sync pytest apps/hostlink/tests/test_consumers.py -q
```
Both must pass. Ignore the `VIRTUAL_ENV ... does not match` warning.

## Constraints
- Do NOT touch the approval/auth/signing logic, `pty_session.send_keys`, or the
  offline-queue draining code. Scope is strictly: the two files above + their tests.
- No AI attribution anywhere. Do not commit, push, or open a PR — stop after tests pass.
- If the existing daemon tests live under a different path/fixture style than
  assumed, ADAPT to the existing style rather than inventing a new harness.
