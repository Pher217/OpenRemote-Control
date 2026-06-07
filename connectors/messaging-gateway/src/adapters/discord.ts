import { Client, GatewayIntentBits, TextChannel } from "discord.js";
import type { Adapter, InboundHandler } from "./types.js";

export class DiscordAdapter implements Adapter {
  readonly platform = "discord";

  private readonly token: string;
  private client: Client | null = null;

  constructor(token: string) {
    this.token = token;
  }

  async start(onInbound: InboundHandler): Promise<void> {
    const client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent, // required to read message.content
      ],
    });

    this.client = client;

    client.on("messageCreate", async (message) => {
      // Ignore bots (including our own bot user)
      if (message.author.bot) return;

      const channelId = message.channelId;
      const sender = message.author.tag; // e.g. "Username#1234" or "username" on newer Discord
      const text = message.content;

      if (!text) return;

      try {
        await onInbound(channelId, sender, text);
      } catch (err) {
        console.error("[discord] onInbound error:", err);
      }
    });

    await client.login(this.token);
    console.log("[discord] Logged in.");
  }

  async sendMessage(recipient: string, text: string): Promise<void> {
    if (!this.client) {
      throw new Error("[discord] Client not started; cannot send message.");
    }
    // recipient is the channel ID string
    const channel = await this.client.channels.fetch(recipient);
    if (!channel || !(channel instanceof TextChannel)) {
      throw new Error(`[discord] Channel ${recipient} not found or is not a text channel.`);
    }
    await channel.send(text);
  }
}
