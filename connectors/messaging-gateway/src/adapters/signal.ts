// Signal adapter using signal-cli-rest-api (bbernhard/signal-cli-rest-api).
//
// Requirements:
//   - A running signal-cli-rest-api instance reachable at SIGNAL_API_URL.
//   - The number SIGNAL_NUMBER must already be registered/linked in that instance.
//   - See: https://github.com/bbernhard/signal-cli-rest-api
//
// Inbound strategy:
//   Prefer the WebSocket endpoint `ws(s)://${host}/v1/receive/${SIGNAL_NUMBER}` which
//   delivers messages as JSON envelopes in real-time.  If the WS connection closes we
//   reconnect after a short back-off.  Polling via GET /v1/receive is NOT used in this
//   implementation; the WS endpoint is available in all recent versions of the image.

import { IncomingMessage } from "node:http";
import { WebSocket } from "ws";
import type { Adapter, InboundHandler } from "./types.js";

// ---------------------------------------------------------------------------
// Pure envelope-parsing helpers (exported for unit tests)
// ---------------------------------------------------------------------------

export interface SignalEnvelope {
  envelope?: {
    source?: string;
    sourceNumber?: string;
    dataMessage?: {
      message?: string | null;
    };
  };
}

/**
 * Extract (chatId, sender, text) from a raw signal-cli-rest-api WS message.
 * Returns null when the message is not a plain data message (e.g. typing, receipt).
 */
export function parseSignalEnvelope(
  raw: unknown
): { chatId: string; sender: string; text: string } | null {
  if (typeof raw !== "object" || raw === null) return null;

  const obj = raw as SignalEnvelope;
  const env = obj.envelope;
  if (!env) return null;

  const dataMsg = env.dataMessage;
  if (!dataMsg) return null; // receipt, typing indicator, etc.

  const text = dataMsg.message;
  if (typeof text !== "string" || text.trim() === "") return null;

  // sourceNumber takes precedence; source is the UUID-based identifier
  const sender = env.sourceNumber ?? env.source ?? "";
  if (!sender) return null;

  return { chatId: sender, sender, text };
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class SignalAdapter implements Adapter {
  readonly platform = "signal";

  private readonly apiUrl: string;
  private readonly number: string;
  private ws: WebSocket | null = null;
  private stopped = false;

  constructor(apiUrl: string, number: string) {
    this.apiUrl = apiUrl.replace(/\/$/, "");
    this.number = number;
  }

  async start(onInbound: InboundHandler): Promise<void> {
    this.stopped = false;
    this.connectWs(onInbound);
    // start() resolves immediately; WS runs in background like the other adapters
  }

  private connectWs(onInbound: InboundHandler, delayMs = 0): void {
    if (this.stopped) return;

    setTimeout(() => {
      if (this.stopped) return;

      // Convert http(s):// to ws(s)://
      const wsUrl =
        this.apiUrl.replace(/^https:\/\//, "wss://").replace(/^http:\/\//, "ws://") +
        `/v1/receive/${encodeURIComponent(this.number)}`;

      console.log(`[signal] Connecting to ${wsUrl}`);
      const ws = new WebSocket(wsUrl);
      this.ws = ws;

      ws.on("message", (data: Buffer | string) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(typeof data === "string" ? data : data.toString("utf8"));
        } catch {
          return; // malformed frame — ignore
        }

        const result = parseSignalEnvelope(parsed);
        if (!result) return;

        onInbound(result.chatId, result.sender, result.text).catch((err: unknown) => {
          console.error("[signal] onInbound error:", err);
        });
      });

      ws.on("open", () => {
        console.log("[signal] WebSocket connected.");
      });

      ws.on("error", (err: Error) => {
        console.error("[signal] WebSocket error:", err.message);
      });

      ws.on("close", (code: number, reason: Buffer) => {
        if (this.stopped) return;
        const reasonStr = reason.length ? reason.toString() : "(no reason)";
        console.warn(`[signal] WebSocket closed (${code} ${reasonStr}). Reconnecting in 5 s.`);
        this.connectWs(onInbound, 5000);
      });
    }, delayMs);
  }

  async sendMessage(recipient: string, text: string): Promise<void> {
    const url = `${this.apiUrl}/v2/send`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        number: this.number,
        recipients: [recipient],
      }),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`[signal] sendMessage failed: ${res.status} ${res.statusText} — ${body}`);
    }
  }

  /** Graceful shutdown — stops reconnect loop. */
  stop(): void {
    this.stopped = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
