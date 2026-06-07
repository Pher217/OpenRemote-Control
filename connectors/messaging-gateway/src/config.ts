export interface Config {
  backendUrl: string;
  gatewayToken: string;
  enabledPlatforms: Set<string>;
  pollIntervalMs: number;
  slackBotToken: string;
  slackAppToken: string;
  discordToken: string;
  signalApiUrl: string;
  signalNumber: string;
  blueBubblesUrl: string;
  blueBubblesPassword: string;
  iMessageWebhookPort: number;
}

function requireEnv(key: string): string {
  const val = process.env[key];
  if (!val) throw new Error(`Required env var ${key} is not set`);
  return val;
}

function optionalEnv(key: string, fallback = ""): string {
  return process.env[key] ?? fallback;
}

export function loadConfig(): Config {
  const backendUrl = requireEnv("BACKEND_URL").replace(/\/$/, "");
  const gatewayToken = requireEnv("MESSAGING_GATEWAY_TOKEN");

  const platformsCsv = optionalEnv("ENABLED_PLATFORMS", "whatsapp,slack,discord,signal,imessage");
  const enabledPlatforms = new Set(
    platformsCsv
      .split(",")
      .map((p) => p.trim().toLowerCase())
      .filter(Boolean)
  );

  const pollIntervalMs = parseInt(optionalEnv("POLL_INTERVAL_MS", "5000"), 10);

  return {
    backendUrl,
    gatewayToken,
    enabledPlatforms,
    pollIntervalMs,
    slackBotToken: optionalEnv("SLACK_BOT_TOKEN"),
    slackAppToken: optionalEnv("SLACK_APP_TOKEN"),
    discordToken: optionalEnv("DISCORD_TOKEN"),
    signalApiUrl: optionalEnv("SIGNAL_API_URL"),
    signalNumber: optionalEnv("SIGNAL_NUMBER"),
    blueBubblesUrl: optionalEnv("BLUEBUBBLES_URL"),
    blueBubblesPassword: optionalEnv("BLUEBUBBLES_PASSWORD"),
    iMessageWebhookPort: parseInt(optionalEnv("IMESSAGE_WEBHOOK_PORT", "3001"), 10),
  };
}
