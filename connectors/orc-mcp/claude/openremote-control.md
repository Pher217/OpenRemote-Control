---
description: Start an OpenRemote-Control session and dispatch it to your chat app
---

Call the `openremote_control` tool from the `orc` MCP server to start a remote-control
session for this coding session and dispatch it to my messaging app of choice
(Telegram, or WhatsApp/Slack/Signal via Matrix), so I can supervise AND drive it from
my phone.

The tool binds the chat to THIS coding session (via `CLAUDE_CODE_SESSION_ID`): a reply
typed in the topic runs `claude -p --resume <this-session>` in the workspace and streams
the answer back — write + stream, continuing this exact conversation. (Note: turns driven
from the phone won't appear live in the editor panel — it's a handoff, not a two-way live
mirror; and don't type in both at once.)

If "$ARGUMENTS" is non-empty, pass it as the session `name`. Otherwise call the tool
with no name and let the backend auto-name the session.

After the tool returns, report the confirmation it gives (the session name, or the
error sentinel if dispatch failed). Do not take any other action.
