// iMessage adapter using BlueBubbles Server (https://bluebubbles.app).
//
// Requirements:
//   - A Mac running BlueBubbles Server, reachable at BLUEBUBBLES_URL.
//   - iMessage is only available on macOS — BlueBubbles is the only viable
//     unofficial API surface; there is no first-party cross-platform iMessage API.
//   - In BlueBubbles Server → Settings → Private API / Webhooks, add this gateway's
//     webhook URL (e.g. http://gateway-host:IMESSAGE_WEBHOOK_PORT/webhook).
//   - Select "New Message" as the event type.
//
// Inbound strategy:
//   An HTTP webhook listener (node:http) runs on IMESSAGE_WEBHOOK_PORT (default 3001).
//   BlueBubbles POSTs new-message events to it as JSON.  This approach requires no
//   extra npm dependency (no socket.io-client) and works with all BlueBubbles versions
//   that support outgoing webhooks.
//
// Payload-shape assumptions (BlueBubbles ≥ 1.9):
//   { type: "new-message", data: { text, handle: { address }, chats: [{ guid }] } }
//   All fields are treated as optional; missing/null values cause the event to be
//   silently skipped.

import { createServer, IncomingMessage, ServerResponse } from "node:http";
import type { Adapter, InboundHandler } from "./types.js";

// ---------------------------------------------------------------------------
// Pure payload-parsing helpers (exported for unit tests)
// ---------------------------------------------------------------------------

export interface BlueBubblesMessageEvent {
  type?: string;
  data?: {
    text?: string | null;
    isFromMe?: boolean;
    handle?: { address?: string | null } | null;
    chats?: Array<{ guid?: string | null }> | null;
  };
}

/**
 * Extract (chatId, sender, text) from a BlueBubbles new-message webhook payload.
 * Returns null for non-message events, outgoing messages, or malformed payloads.
 */
export function parseBlueBubblesEvent(
  raw: unknown
): { chatId: string; sender: string; text: string } | null {
  if (typeof raw !== "object" || raw === null) return null;

  const obj = raw as BlueBubblesMessageEvent;

  // Only handle new-message events
  if (obj.type !== "new-message") return null;

  const data = obj.data;
  if (!data) return null;

  // Skip messages we sent
  if (data.isFromMe === true) return null;

  const text = data.text;
  if (typeof text !== "string" || text.trim() === "") return null;

  const sender = data.handle?.address ?? "";
  if (!sender) return null;

  // chatId is the first chat GUID; fall back to the sender's address
  const chatId = data.chats?.[0]?.guid ?? sender;

  return { chatId, sender, text };
}

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

export class IMessageAdapter implements Adapter {
  readonly platform = "imessage";

  private readonly bbUrl: string;
  private readonly password: string;
  private readonly webhookPort: number;

  constructor(bbUrl: string, password: string, webhookPort = 3001) {
    this.bbUrl = bbUrl.replace(/\/$/, "");
    this.password = password;
    this.webhookPort = webhookPort;
  }

  async start(onInbound: InboundHandler): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const server = createServer((req: IncomingMessage, res: ServerResponse) => {
        if (req.method !== "POST") {
          res.writeHead(405);
          res.end();
          return;
        }

        const chunks: Buffer[] = [];
        req.on("data", (chunk: Buffer) => chunks.push(chunk));
        req.on("end", () => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          } catch {
            res.writeHead(400);
            res.end();
            return;
          }

          const result = parseBlueBubblesEvent(parsed);
          if (result) {
            onInbound(result.chatId, result.sender, result.text).catch((err: unknown) => {
              console.error("[imessage] onInbound error:", err);
            });
          }

          res.writeHead(200);
          res.end();
        });

        req.on("error", (err: Error) => {
          console.error("[imessage] webhook request error:", err.message);
          res.writeHead(500);
          res.end();
        });
      });

      server.once("error", reject);
      server.listen(this.webhookPort, () => {
        console.log(`[imessage] Webhook listener started on port ${this.webhookPort}.`);
        resolve();
      });
    });
  }

  async sendMessage(recipient: string, text: string): Promise<void> {
    // recipient may be a chat GUID ("iMessage;-;+15551234567") or a bare handle/address.
    // BlueBubbles send-text endpoint: POST /api/v1/message/text?password=...
    const url = `${this.bbUrl}/api/v1/message/text?password=${encodeURIComponent(this.password)}`;

    // Determine whether recipient looks like a chat GUID or a bare handle address.
    const isGuid = recipient.includes(";");
    const body = isGuid
      ? { chatGuid: recipient, message: text, method: "apple-script" }
      : { address: recipient, message: text, method: "apple-script" };

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const bodyText = await res.text().catch(() => "");
      throw new Error(
        `[imessage] sendMessage failed: ${res.status} ${res.statusText} — ${bodyText}`
      );
    }
  }
}
