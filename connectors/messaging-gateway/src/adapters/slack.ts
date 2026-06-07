import { App } from "@slack/bolt";
import type { Adapter, InboundHandler } from "./types.js";

export class SlackAdapter implements Adapter {
  readonly platform = "slack";

  private readonly botToken: string;
  private readonly appToken: string;
  private app: App | null = null;

  constructor(botToken: string, appToken: string) {
    this.botToken = botToken;
    this.appToken = appToken;
  }

  async start(onInbound: InboundHandler): Promise<void> {
    const app = new App({
      token: this.botToken,
      appToken: this.appToken,
      // Socket Mode: no public HTTP endpoint required; uses a long-lived WebSocket connection.
      socketMode: true,
    });

    this.app = app;

    // The "message" event fires for all messages visible to the bot.
    // subtype is absent on regular user messages; filter out bot_message etc.
    app.message(async ({ message, say: _say }) => {
      // @slack/bolt types message as a union; cast to the common shape we need
      const msg = message as {
        subtype?: string;
        channel: string;
        user?: string;
        text?: string;
        bot_id?: string;
      };

      // Skip messages posted by bots (including our own)
      if (msg.subtype === "bot_message" || msg.bot_id) return;

      const channel = msg.channel;
      const sender = msg.user ?? "unknown";
      const text = msg.text ?? "";

      if (!text) return;

      try {
        await onInbound(channel, sender, text);
      } catch (err) {
        console.error("[slack] onInbound error:", err);
      }
    });

    await app.start();
    console.log("[slack] Connected via Socket Mode.");
  }

  async sendMessage(recipient: string, text: string): Promise<void> {
    if (!this.app) {
      throw new Error("[slack] App not started; cannot send message.");
    }
    await this.app.client.chat.postMessage({
      channel: recipient,
      text,
    });
  }
}
