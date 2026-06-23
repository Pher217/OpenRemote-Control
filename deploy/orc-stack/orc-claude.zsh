# orc-claude.zsh — make `claude` driveable + streaming in Telegram BY DEFAULT.
#
# Source this from ~/.zshrc:
#     source <repo>/deploy/orc-stack/orc-claude.zsh
#
# Behaviour:
#   claude               -> launches the REAL Claude inside an orc-owned tmux PTY
#                           (Telegram input + streaming, the ✏️ topic) and attaches
#                           you locally, so you get the normal TUI *and* remote drive.
#                           Detach with Ctrl-b d and it keeps running (still driveable).
#   claude <any args...> -> runs the real binary untouched (headless `-p`, `--resume`,
#                           `mcp`, `--version`, pipes, scripts). No orc, no recursion.
#
# Why a launcher and not a stack flag: orc can only inject input into a session it
# launched itself in a PTY. Sessions started by the VSCode extension or a bare
# terminal have no PTY orc owns and can only be mirrored (👁), never driven. Routing
# the launch through orc is the only way to make a session writable.
#
# Requires: tmux + the host-agent venv's orc-host (override path with $ORC_HOST_BIN).

# Derive orc-host from this file's location (<repo>/deploy/orc-stack/orc-claude.zsh
# -> <repo>/host-agent/.venv/bin/orc-host); override with $ORC_HOST_BIN.
ORC_HOST_BIN="${ORC_HOST_BIN:-${${(%):-%x}:A:h:h:h}/host-agent/.venv/bin/orc-host}"

claude() {
  emulate -L zsh

  # Resolve the real binary, bypassing this function (zsh -p = PATH search only).
  local real_claude
  real_claude="$(whence -p claude 2>/dev/null)"
  if [[ -z "$real_claude" ]]; then
    print -u2 "claude: real binary not found on PATH"
    return 127
  fi

  # Pass-through: any args, or a non-tty stdin/stdout (pipe / script / headless use).
  if (( $# > 0 )) || [[ ! -t 0 || ! -t 1 ]]; then
    "$real_claude" "$@"
    return
  fi

  # Graceful degrade if the drive path is unavailable.
  if [[ ! -x "$ORC_HOST_BIN" ]] || ! command -v tmux >/dev/null 2>&1; then
    print -u2 "claude: orc-host/tmux unavailable — launching plain claude (not driveable)"
    "$real_claude"
    return
  fi

  local name="claude-$(date +%H%M%S)-$$-$RANDOM"
  local logf="${TMPDIR:-/tmp}/orc-claude-$name.log"
  print -u2 "↗ orc session '$name' — driveable from Telegram (✏️). Detach: Ctrl-b d (keeps running)."

  # Background the orc ws/stream loop; it lives until the tmux session ends.
  "$ORC_HOST_BIN" run --name "$name" --cwd "$PWD" "$real_claude" >"$logf" 2>&1 &
  local orc_pid=$!

  # Wait for the tmux session to actually come up (poll, don't blind-sleep). Bail
  # with the log path if orc-host dies early or the session never appears.
  local i
  for i in {1..20}; do
    tmux has-session -t "$name" 2>/dev/null && break
    kill -0 "$orc_pid" 2>/dev/null \
      || { print -u2 "claude: orc-host exited before the session started — see $logf"; return 1; }
    sleep 0.25
  done
  if ! tmux has-session -t "$name" 2>/dev/null; then
    print -u2 "claude: tmux session '$name' did not start in time — see $logf"
    disown "$orc_pid" 2>/dev/null
    return 1
  fi

  if [[ -n "$TMUX" ]]; then
    # Inside tmux already — don't nest-attach; hand off the session name.
    print -u2 "  (inside tmux) attach in another pane:  tmux attach -t $name"
    disown "$orc_pid" 2>/dev/null
    return
  fi

  tmux attach -t "$name"
  # attach returned: either the user detached (session still alive, keep it running
  # and Telegram-driveable) or Claude exited (orc-host self-terminates on stream EOF).
  # Either way, release the background job so the prompt returns immediately.
  disown "$orc_pid" 2>/dev/null
}
