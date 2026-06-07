---
description: Start an OpenRemote-Control session and dispatch it to your chat app
---

Call the `openremote_control` tool from the `orc` MCP server to start a remote-control
session for this coding session and dispatch it to my messaging app of choice
(Telegram, or WhatsApp/Slack/Signal via Matrix), so I can supervise from my phone.

If "$ARGUMENTS" is non-empty, pass it as the session `name`. Otherwise call the tool
with no name and let the backend auto-name the session.

After the tool returns, report the confirmation it gives (the session name, or the
error sentinel if dispatch failed). Do not take any other action.
