import { loadConfig } from "./config.js";
import { BackendClient } from "./backend.js";
import { WhatsAppAdapter } from "./adapters/whatsapp.js";
import { SlackAdapter } from "./adapters/slack.js";
import { DiscordAdapter } from "./adapters/discord.js";
import { SignalAdapter } from "./adapters/signal.js";
import { IMessageAdapter } from "./adapters/imessage.js";
import type { Adapter } from "./adapters/types.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const backend = new BackendClient(config.backendUrl, config.gatewayToken);

  const adapters: Adapter[] = [];

  if (config.enabledPlatforms.has("whatsapp")) {
    adapters.push(new WhatsAppAdapter("./data/whatsapp"));
  }
  if (config.enabledPlatforms.has("slack")) {
    if (!config.slackBotToken || !config.slackAppToken) {
      console.warn("[gateway] SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — skipping Slack.");
    } else {
      adapters.push(new SlackAdapter(config.slackBotToken, config.slackAppToken));
    }
  }
  if (config.enabledPlatforms.has("discord")) {
    if (!config.discordToken) {
      console.warn("[gateway] DISCORD_TOKEN not set — skipping Discord.");
    } else {
      adapters.push(new DiscordAdapter(config.discordToken));
    }
  }
  if (config.enabledPlatforms.has("signal")) {
    if (!config.signalApiUrl || !config.signalNumber) {
      console.warn("[gateway] SIGNAL_API_URL / SIGNAL_NUMBER not set — skipping Signal.");
    } else {
      adapters.push(new SignalAdapter(config.signalApiUrl, config.signalNumber));
    }
  }
  if (config.enabledPlatforms.has("imessage")) {
    if (!config.blueBubblesUrl || !config.blueBubblesPassword) {
      console.warn("[gateway] BLUEBUBBLES_URL / BLUEBUBBLES_PASSWORD not set — skipping iMessage.");
    } else {
      adapters.push(
        new IMessageAdapter(config.blueBubblesUrl, config.blueBubblesPassword, config.iMessageWebhookPort)
      );
    }
  }

  if (adapters.length === 0) {
    console.error("[gateway] No adapters enabled. Set ENABLED_PLATFORMS and required tokens.");
    process.exit(1);
  }

  // Inbound handler: relay message from platform to backend; send reply back if present.
  function makeInboundHandler(adapter: Adapter) {
    return async (chatId: string, sender: string, text: string): Promise<void> => {
      try {
        const reply = await backend.postInbound(adapter.platform, chatId, sender, text);
        if (reply) {
          await adapter.sendMessage(chatId, reply);
        }
      } catch (err) {
        console.error(`[${adapter.platform}] inbound relay error:`, err);
      }
    };
  }

  // Start each adapter in isolation so one failure doesn't kill the others.
  for (const adapter of adapters) {
    try {
      await adapter.start(makeInboundHandler(adapter));
      console.log(`[gateway] ${adapter.platform} adapter started.`);
    } catch (err) {
      console.error(`[gateway] Failed to start ${adapter.platform} adapter:`, err);
    }
  }

  // Poll outbox per enabled adapter.
  for (const adapter of adapters) {
    startPollLoop(adapter, backend, config.pollIntervalMs);
  }

  console.log("[gateway] All adapters started. Polling every", config.pollIntervalMs, "ms.");
}

function startPollLoop(
  adapter: Adapter,
  backend: BackendClient,
  intervalMs: number
): void {
  const poll = async (): Promise<void> => {
    try {
      const messages = await backend.pollOutbox(adapter.platform);
      for (const msg of messages) {
        try {
          await adapter.sendMessage(msg.recipient, msg.text);
        } catch (err) {
          console.error(`[${adapter.platform}] Failed to deliver outbox message ${msg.id}:`, err);
        }
      }
    } catch (err) {
      console.error(`[${adapter.platform}] pollOutbox error:`, err);
    } finally {
      setTimeout(() => void poll(), intervalMs);
    }
  };

  void poll();
}

main().catch((err) => {
  console.error("[gateway] Fatal:", err);
  process.exit(1);
});
