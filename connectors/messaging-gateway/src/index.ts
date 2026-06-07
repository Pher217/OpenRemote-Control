import { loadConfig } from "./config.js";
import { startSetupServer } from "./setup-server.js";
import { setStatus } from "./setup-state.js";
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

  // Start setup page early so it's reachable while adapters are still connecting.
  const setupServer = startSetupServer(config.setupPort, config.enabledPlatforms);
  setupServer.on("listening", () => {
    console.log(`[gateway] Setup page: http://localhost:${config.setupPort}`);
  });

  const adapters: Adapter[] = [];

  if (config.enabledPlatforms.has("whatsapp")) {
    adapters.push(new WhatsAppAdapter("./data/whatsapp"));
    // WhatsApp status is managed via connection events in the adapter itself.
  }
  if (config.enabledPlatforms.has("slack")) {
    if (!config.slackBotToken || !config.slackAppToken) {
      console.warn("[gateway] SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — skipping Slack.");
      setStatus("slack", "needs_token", "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN");
    } else {
      adapters.push(new SlackAdapter(config.slackBotToken, config.slackAppToken));
    }
  }
  if (config.enabledPlatforms.has("discord")) {
    if (!config.discordToken) {
      console.warn("[gateway] DISCORD_TOKEN not set — skipping Discord.");
      setStatus("discord", "needs_token", "Set DISCORD_TOKEN");
    } else {
      adapters.push(new DiscordAdapter(config.discordToken));
    }
  }
  if (config.enabledPlatforms.has("signal")) {
    if (!config.signalApiUrl || !config.signalNumber) {
      console.warn("[gateway] SIGNAL_API_URL / SIGNAL_NUMBER not set — skipping Signal.");
      setStatus("signal", "needs_token", "Set SIGNAL_API_URL and SIGNAL_NUMBER");
    } else {
      adapters.push(new SignalAdapter(config.signalApiUrl, config.signalNumber));
    }
  }
  if (config.enabledPlatforms.has("imessage")) {
    if (!config.blueBubblesUrl || !config.blueBubblesPassword) {
      console.warn("[gateway] BLUEBUBBLES_URL / BLUEBUBBLES_PASSWORD not set — skipping iMessage.");
      setStatus("imessage", "needs_token", "Set BLUEBUBBLES_URL and BLUEBUBBLES_PASSWORD");
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
      // WhatsApp manages its own status via connection events; mark others linked here.
      if (adapter.platform !== "whatsapp") {
        setStatus(adapter.platform, "linked");
      }
    } catch (err) {
      console.error(`[gateway] Failed to start ${adapter.platform} adapter:`, err);
      setStatus(adapter.platform, "error", String(err));
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
