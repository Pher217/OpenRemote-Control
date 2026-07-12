# Spec — ORC Pets: Open-Source Cross-Platform Desktop Agent Companions

## Overview

ORC Pets are native desktop companions for AI coding agents — small animated
characters that live on your screen, one per active agent session, giving you
a always-visible, always-on surface for supervision and control. They are
not just notification bubbles: they are a full interaction surface with
right-click menus, inline chat, approval dialogs, and session management.

Built as a new surface for OpenRemote-Control, they plug into the existing
backend alongside Telegram and the messaging-gateway. The backend already
aggregates `notify` / `ask_human` / `request_approval` events from every
agent via the orc-mcp bridge and host-agent daemon — the pet is simply
another way to receive and respond to those same events, natively on your
desktop.

## Motivation

Telegram is excellent for remote supervision from your phone, but when you
are at your desk you want something ambient — a presence that catches your
eye when an agent needs you, without forcing you to switch to a chat app.
A desktop pet sits at the edge of your vision and reacts: it perks up when
an agent has a question, looks busy when it is working, looks worried on
errors. Right-click it to start a new session, open a chat window, view
the transcript, or kill the session. No context switch, no window hunting.

## Architecture

```
  AI agents (Codex / Claude / Gemini / …)
    │
    ├── orc-mcp bridge ──────────────▶ ORC Backend (Django/DRF/Channels)
    │     openremote_control()              │  prompts (notice/ask/approve)
    │     notify()                           │  threads + messages
    │     ask_human()                        │  host-agent WebSocket consumers
    │     request_approval()                 │  observe: turn delivery
    │                                        │
    └── host-agent daemon ──────────▶        │
          PTY streaming                      │
          headless engines                   │
                                           │
              ┌────────────────────────────┐ │
              │  ORC Pet App (Tauri)        │ │
              │  ┌──────────────┐          │ │
              │  │ WebSocket     │◀────────┼─┘
              │  │ consumer      │  events  │
              │  │ (per pet)     │────────▶│  responses
              │  └──────┬───────┘          │ │
              │         │                  │ │
              │  ┌──────▼───────┐          │ │
              │  │ Pet State     │          │ │
              │  │ Machine       │          │ │
              │  └──────┬───────┘          │ │
              │         │                  │ │
              │  ┌──────▼───────┐  ┌──────┐│ │
              │  │ Renderer      │  │ Right││ │
              │  │ (sprite/Lottie│  │ Click ││ │
              │  │  animation)  │  │ Menu  ││ │
              │  └──────────────┘  └──────┘│ │
              └────────────────────────────┘ │
```

### Connection model

The pet app authenticates to the ORC backend the same way `orc-mcp` does:
Ed25519 keypair, registered via a pairing code. On launch it opens a
WebSocket connection to a new `ws/pets/<connector_id>/` endpoint (a new
Channels consumer, modeled on the existing hostlink consumer but
read-only for events, read-write for prompt responses).

The backend pushes three event types to the pet over this WebSocket:

| Event | Source | Pet reaction |
|---|---|---|
| `prompt.created` | connectors service `_deliver` | Pet perks up, shows speech bubble with the question + interactive controls |
| `turn.event` | observe delivery | Pet animates (working/thinking), optional mini-digest in bubble |
| `thread.status` | Thread.status changes | Pet changes state (running → waiting → completed → failed) |

The pet sends back:

| Message | Triggers |
|---|---|
| `prompt.response` | User clicks an approval button, picks a choice, or types in the chat bubble |
| `session.start` | User right-clicks → "New session" |
| `session.kill` | User right-clicks → "Kill session" |
| `chat.send` | User types in the pet's inline chat window |

### Backend changes (minimal)

The backend already has all the primitives. The changes are:

1. **New WebSocket consumer** `PetConsumer` in a new `apps/pets/` app (or
   extend `apps/connectors/`). Authenticated via the same Ed25519
   connector key system. Joins a channel group per `connector_id`. On
   connect, replays the last N pending prompts for that connector's
   threads.

2. **Extend `_deliver()`** in `connectors/service.py` to fan out to the
   pet WebSocket in addition to (or instead of) Telegram. The routing
   logic in `apps/messaging/routing.py` already supports an "active
   platform" concept — add `pet` as a platform. A pet can run alongside
   Telegram (both receive the event) or replace it when the operator is
   at their desk.

3. **New API endpoints** (all under `/api/pets/`):
   - `POST /api/pets/pair` — pairing code exchange (reuse existing
     `orc_pair` mechanism)
   - `GET /api/pets/threads` — list active threads with their status +
     provider + last turn digest
   - `POST /api/pets/sessions/start` — start a new session (dispatches
     to host-agent, same as `/openremote-control`)
   - `POST /api/pets/sessions/{thread_id}/kill` — kill a session
   - `POST /api/pets/prompts/{nonce}/resolve` — resolve a prompt
     (reuse existing `resolve()` in prompts service)
   - `WS /ws/pets/<connector_id>/` — event stream

4. **No new models.** The pet reuses `ConnectorInstance`, `Thread`,
   `Prompt`, `Message`. The pet's connector_id is just another connector
   with `tool="pet"`. Sessions started from the pet create threads with
   `runtime_mode=RC` (Remote Control), same as orc-mcp dispatches.

## Pet state machine

Each pet has a visual state driven by the thread's status and recent
events:

```
                    ┌─────────┐
          ┌────────▶│ sleeping │◀── no events for 5+ min
          │         └────┬────┘
          │              │ event arrives
          │              ▼
          │         ┌──────────┐
          │         │  idle    │─── pet sits, occasional idle animation
          │         └────┬─────┘
          │              │ turn starts
          │              ▼
          │         ┌──────────┐
          │         │ working  │─── pet types, looks at screen, progress bar
          │         └────┬─────┘
          │              │ turn completes
          │              ▼
          │         ┌──────────┐
          │         │  done     │─── brief happy animation, then back to idle
          │         └────┬─────┘
          │              │
          │    ┌─────────┼──────────┐
          │    │         │          │
          │    ▼         ▼          ▼
          │ ┌─────┐ ┌─────────┐ ┌────────┐
          │ │error│ │ waiting  │ │notice │
          │ │     │ │ (prompt) │ │       │
          │ └──┬──┘ └────┬────┘ └───┬───┘
          │    │         │          │
          └────┘         │ resolved │
                        ▼          │
                   ┌──────────┐    │
                   │ resolved │────┘
                   └──────────┘
```

States and their visual cues:

| State | Trigger | Visual |
|---|---|---|
| sleeping | No events for 5 min | Pet curls up, Zzz particles, dimmed |
| idle | Default / after resolution | Sitting, occasional blink, idle fidget animations |
| working | `turn.event` with active turn | Pet typing at a miniature screen, progress shimmer |
| done | `turn.completed` | Happy bounce, brief sparkle, returns to idle |
| error | `turn.failed` or `thread.status=failed` | Pet looks worried, red tint, slight shake |
| waiting | `prompt.created` (ask_human or request_approval) | Pet perks up, speech bubble with question + buttons, attention animation (wave, jump) |
| notice | `prompt.created` (notice type) | Speech bubble with message, no interaction needed, auto-dismiss after 10s |

## Right-click context menu

The pet's right-click (or two-finger tap / long-press) menu is the primary
control surface. It adapts to the pet's current state:

### Always available

- **New session...** — opens a small dialog to name a session, select a
  provider (Codex / Claude / Gemini / custom), select a workspace path.
  Dispatches to the backend, which starts the host-agent engine.
- **Open chat** — opens the pet's inline chat window (a small always-on-top
  window with a message history scroll + text input). Messages sent here
  go to the agent's current turn via `chat.send`.
- **Settings...** — pet appearance, animation speed, position lock,
  transparency, "always on top" toggle, sound toggle, notification
  verbosity.
- **About ORC Pets** — version, backend connection status, connector_id.

### When a session is active

- **View transcript** — opens a scrollable window showing the session's
  recent turns (pulled from the observe/tail system).
- **Pause / Resume** — pauses the agent's turn processing (sends a
  hold signal through the backend).
- **Kill session** — confirms, then sends `session.kill`. Pet plays a
  sad-goodbye animation and returns to idle.
- **Switch provider** — re-dispatches the current session to a different
  engine (e.g., swap from Codex to Claude mid-conversation, if the
  backend supports it).

### When waiting (prompt active)

- **Answer...** — focuses the speech bubble's input (same as clicking the
  pet).
- **Dismiss** — cancels the prompt (sends a `prompt.response` with
  "defer" or a timeout signal).
- **Open in Telegram** — if Telegram is also configured, opens the
  session's Telegram topic for a richer mobile-handoff experience.

### When multiple pets are on screen

- **Arrange pets** — auto-arrange all pets along the bottom of the screen,
  evenly spaced, or along the user's preferred screen edge.
- **Group / Ungroup** — cluster pets together or spread them apart.
- **Bring all to front** — all pets animate to their positions and do
  a brief wave.

## Speech bubbles & input

Speech bubbles are the pet's notification surface. They appear above the
pet and auto-position to stay on-screen.

### Bubble types

| Type | Prompt type | Contents | Interaction |
|---|---|---|---|
| Notice | `NOTICE` | Message text, agent name, timestamp | Auto-dismiss 10s, or click to dismiss |
| Question | `FREE_TEXT`, `CHOICE_*` | Question text + options (if any) | Text input or option buttons; "Send" button |
| Approval | `APPROVAL` | Action description + preview (code diff, command) | "Allow" / "Deny" / "Defer" buttons |
| Turn digest | (turn.event) | Compact one-line summary of latest turn | Click to expand full transcript window |

### Approval preview rendering

For `request_approval`, the `preview` field often contains a code diff or
shell command. The bubble renders this in a monospace block with syntax
highlighting (using a lightweight highlighter like Shiki or Highlight.js).
The diff is scrollable if it exceeds the bubble's max height (300px).

### Bubble stacking

When multiple prompts arrive in quick succession (e.g., two agents ask
simultaneously), their bubbles stack vertically with a 4px gap. The
oldest unacknowledged bubble is at the top. Acknowledged bubbles
collapse with a brief animation.

## Multi-provider: one pet per agent

Each active agent session gets its own pet. The backend already tracks
sessions as `Thread` objects with a `runtime` (provider) and
`metadata.provider`. The pet app:

1. On connect, fetches `GET /api/pets/threads` to discover all active
   sessions.
2. Spawns one pet per active thread.
3. Each pet subscribes to its thread's events via the WebSocket (the
   consumer routes events to the correct pet by `thread_id`).
4. When a session completes/fails, the pet plays a goodbye animation and
   despawns after 30s (configurable).

### Provider visual identity

Each provider gets a distinct pet character and accent color. The
characters are community-contributable sprite sheets or Lottie files.
Default set:

| Provider | Character concept | Accent color |
|---|---|---|
| Codex (OpenAI) | A curious fox with a green scarf | `#10A37F` |
| Claude (Anthropic) | A calm cat with a warm orange hue | `#D97757` |
| Gemini (Google) | A playful twin-tailed bird | `#4285F4` |
| Cursor | A cursor-shaped sprite companion | `#6366F1` |
| Custom | User-uploaded sprite sheet | User-chosen |

Characters are animated sprite sheets (PNG atlas) or Lottie JSON files.
Each character has states: `idle`, `working`, `thinking`, `happy`,
`sad`, `sleeping`, `wave`. The renderer picks the animation matching
the pet's current state.

### Simultaneous operation

Multiple pets can run at once, each representing a different agent
session. They are independent — clicking one only interacts with its
session. The arrange feature keeps them organized. When two pets'
bubbles overlap, the more recent one is on top.

## Cross-platform desktop app

### Tech stack: Tauri

**Tauri** (Rust core + webview frontend) is the chosen framework:

- ~10 MB binary vs Electron's ~150 MB
- Uses the system webview (WebKit on macOS, WebView2 on Windows,
  WebKitGTK on Linux) — no Chromium bundled
- Native windowing with transparency and always-on-top support
- Rust backend handles WebSocket connection, Ed25519 signing, file I/O
- React/TypeScript frontend handles rendering, animations, menus

### Platform-specific behavior

| Concern | macOS | Windows | Linux |
|---|---|---|---|
| Transparency | `NSWindow` opacity, supported natively | Layered windows, supported | Compositor-dependent; fallback to opaque |
| Always-on-top | `level = .floating` | `WS_EX_TOPMOST` | `-above` on GNOME; fallback opaque |
| Click-through | `ignoresMouseEvents` | `WS_EX_TRANSPARENT` | X11 input region mask |
| System tray | `NSStatusItem` | `NotifyIcon` | AppIndicator / StatusNotifierItem |
| Autostart | LaunchAgent plist | Registry `Run` key | `.desktop` autostart entry |
| Pet draggable | Custom drag via mouse events on webview | Same | Same |

### Window configuration

Each pet is a borderless, transparent, always-on-top window:

- Size: 128x128 px (pet) + dynamic bubble above
- Position: user-draggable, persists per provider
- Click-through: enabled everywhere except the pet sprite and bubble
- Focus: pet window takes focus only when a bubble has an interactive
  element (approval buttons, text input)

### System tray icon

A single tray icon represents the ORC Pets app as a whole:

- Click: toggle all pets visible/hidden
- Right-click: settings, quit, reconnect, view all sessions
- Badge: number of pending prompts across all pets

## Inline chat window

The "Open chat" action opens a small always-on-top window (320x400 px)
per pet:

```
┌─────────────────────────────────────┐
│ 🦊 Codex — my-feature   [─] [×]     │
├─────────────────────────────────────┤
│                                     │
│  Agent: I'll start by reading the   │
│  existing test file...              │
│                                     │
│  You: make sure to cover the edge    │
│  case where the queue is empty      │
│                                     │
│  Agent: Done. Added 3 new tests.     │
│                                     │
├─────────────────────────────────────┤
│ [Type a message...         ] [Send] │
└─────────────────────────────────────┘
```

This window is a lightweight message history + input. Messages sent here
go to the agent's current turn via `chat.send` (which the backend routes
to the host-agent daemon, which injects the text into the active
engine's `send()` method). Messages received are turn events from the
observe system, rendered as they arrive.

The chat window is optional — the user can supervise entirely through
bubbles and right-click actions if they prefer.

## Security

- **Authentication**: Ed25519 keypair, same as orc-mcp. The pet app
  generates its keypair on first run, registers via a pairing code from
  the backend (`python manage.py orc_pair` or Telegram `/pair`).
- **No credentials stored client-side** beyond the Ed25519 private key
  (mode 0600, in `~/.config/openremote-control/pet_key`).
- **Backend URL** is user-configured; no cloud relay, no telemetry.
- **Approval previews** are rendered client-side from the server-provided
  text — the pet app never executes anything. The preview is display-only.
- **Chat input** goes through the same input-safety policy as Telegram
  (`input_policy.py`) — the backend validates before injecting into the
  PTY/engine.

## Implementation phases

### Phase 1 — MVP (2-3 weeks)

- Tauri app skeleton: borderless transparent window, single pet sprite
- WebSocket consumer `PetConsumer` + `/ws/pets/<connector_id>/` endpoint
- Event types: `prompt.created`, `thread.status`
- Pet states: idle, working, waiting, error
- Speech bubbles: notice + approval (with Allow/Deny buttons)
- Right-click menu: New session, Open chat, Kill session, Settings, Quit
- One provider (Codex) with a basic fox sprite (idle + working + waiting)
- macOS first (transparency + always-on-top working)
- Pairing flow (reuse `orc-mcp pair`)

### Phase 2 — Multi-provider + chat (2-3 weeks)

- Multi-pet: one pet per active thread, auto-spawn/despawn
- Provider visual identity (Claude cat, Gemini bird)
- Inline chat window per pet
- `ask_human` with free-text input in bubble
- `CHOICE_*` rendering in bubble
- Turn digest bubbles
- Right-click: View transcript, Pause/Resume
- Arrange / Group pets
- Windows platform support

### Phase 3 — Polish + Linux (2-3 weeks)

- Linux platform support (Wayland/X11)
- Lottie animation support (smoother than sprite sheets)
- Sound effects (optional, per-event)
- System tray icon with badge count
- Autostart on all platforms
- Pet customization: upload custom sprite/Lottie, color picker
- Community character pack repository (GitHub repo with contributed pets)
- Sleep / wake scheduling (pets auto-sleep outside working hours)

### Phase 4 — Advanced (future)

- Drag-and-drop files onto a pet to attach context
- Pet-to-pet interaction (when two agents collaborate)
- Voice input (whisper to your pet)
- Pet "moods" based on agent performance (happy streak, frustrated on
  repeated failures)
- Screen-edge gravity (pets "walk" along the bottom edge, can be knocked
  off-screen for fun)
- Notification escalation (if a prompt is ignored for N minutes, pet
  walks to the center of the screen and waves)

## File structure

```
pets/                           # New top-level directory (like host-agent/, connectors/)
  src-tauri/                    # Rust backend
    src/
      main.rs                   # Tauri entry, window management
      ws.rs                     # WebSocket client to ORC backend
      signing.rs                # Ed25519 request signing
      config.rs                 # Pet app config (backend URL, key path, pet positions)
    tauri.conf.json             # Window config (transparent, always-on-top)
    Cargo.toml
  src/                          # React/TypeScript frontend
    App.tsx                     # Root, manages pet instances
    Pet.tsx                     # Single pet: state machine, animation, bubble, menu
    components/
      SpeechBubble.tsx          # Bubble renderer (notice/question/approval)
      ChatWindow.tsx            # Inline chat window
      ContextMenu.tsx           # Right-click menu
      ApprovalPreview.tsx       # Code diff / command preview with syntax highlight
      PetRenderer.tsx           # Sprite/Lottie animation renderer
    hooks/
      usePetEvents.ts           # WebSocket event → state machine
      usePromptResolution.ts    # Prompt response submission
    stores/
      petStore.ts               # Zustand store: pets, threads, prompts
    types/
      events.ts                 # Event type definitions (mirrors backend)
    assets/
      pets/                     # Sprite sheets / Lottie files per provider
        codex-fox/
        claude-cat/
        gemini-bird/
  package.json
  tsconfig.json
  vite.config.ts
```

## Backend file structure

```
backend/apps/pets/
  __init__.py
  apps.py
  consumers.py               # PetConsumer (WebSocket)
  urls.py                    # /api/pets/* endpoints
  views.py                   # REST views (threads, sessions, prompts)
  routing.py                 # WebSocket URL routing
  tests/
    test_consumer.py
    test_views.py
```

## Testing

- **Backend**: Django test suite (same runner as existing backend tests).
  Test the PetConsumer WebSocket, the prompt fan-out to pet, session
  start/kill from pet API, and auth.
- **Pet app (Rust)**: `cargo test` for signing, config, WebSocket
  reconnection logic.
- **Pet app (frontend)**: Vitest for React components, Playwright for
  e2e (bubble rendering, context menu, chat window).
- **CI**: Add a `pets` job to `.github/workflows/ci.yml` — `cargo test`
  + `npm test` + `cargo build` (cross-compile for macOS + Windows).

## Open questions

1. **Should the pet be a standalone app or a feature of the host-agent?**
   Standalone is cleaner — the host-agent runs per-machine, but a pet is
   per-user-session. A laptop with two users could have two pet apps with
   different providers.

2. **Should pets share a single WebSocket or one per pet?**
   Single WebSocket multiplexed by `thread_id` is simpler and uses fewer
   connections. The backend's channel-layer group routing already supports
   this pattern.

3. **Should the pet replace Telegram or augment it?**
   Configurable. The routing layer can deliver to both simultaneously
   (pet for desk presence, Telegram for mobile). A "desk mode" toggle
   suppresses Telegram delivery when the pet is connected and active.

4. **Animation format: sprite sheets or Lottie?**
   Phase 1 ships sprite sheets (PNG atlas) — simpler, universally
   supported, no extra dependencies. Phase 3 adds Lottie for smoother
   vector animations. The renderer abstracts both behind a common
   interface.

5. **Community pet characters — licensing?**
   Default characters are original designs (no IP risk). Community
   contributions go through a contribution guide with a CC-BY license
   for character art. The repo includes a `pets/CONTRIBUTING_PETS.md`.
