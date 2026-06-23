# orc-claude.zsh — make `claude` driveable + streaming in Telegram BY DEFAULT.
#
# Source this from ~/.zshrc:
#     source <repo>/deploy/orc-stack/orc-claude.zsh
#
# Behaviour:
#   claude               -> launches the REAL Claude inside an orc-owned tmux PTY
#                           (Telegram input + streaming, the ✏️ topic) and attaches
#                           you locally, so you get the normal TUI *and* remote drive.
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

  # Pass-through: any args, or a non-tty stdout (pipe / script / headless use).
  if (( $# > 0 )) || [[ ! -t 1 ]]; then
    "$real_claude" "$@"
    return
  fi

  # Graceful degrade if the drive path is unavailable.
  if [[ ! -x "$ORC_HOST_BIN" ]] || ! command -v tmux >/dev/null 2>&1; then
    print -u2 "claude: orc-host/tmux unavailable — launching plain claude (not driveable)"
    "$real_claude"
    return
  fi

  local name="claude-$(date +%H%M%S)-$$"
  print -u2 "↗ orc session '$name' — driveable from Telegram (✏️). Detach: Ctrl-b d (keeps running)."

  # Background the orc ws/stream loop; it lives until the tmux session ends.
  "$ORC_HOST_BIN" run --name "$name" --cwd "$PWD" "$real_claude" >/dev/null 2>&1 &
  local orc_pid=$!
  sleep 1

  if [[ -n "$TMUX" ]]; then
    # Inside tmux already — don't nest-attach.
    print -u2 "  (inside tmux) attach in another pane:  tmux attach -t $name"
    return
  fi

  tmux attach -t "$name" 2>/dev/null \
    || print -u2 "claude: tmux session '$name' not ready — check 'tmux ls'"

  # User exited Claude / killed the session: tidy the background loop.
  wait "$orc_pid" 2>/dev/null
}
