import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import qrcodeTerminal from "qrcode-terminal";
import type { Adapter, InboundHandler } from "./types.js";

// Baileys v7 rc-series API notes:
//   - makeWASocket is the default (and named) export.
//   - "messages.upsert" event: { messages: WAMessage[]; type: MessageUpsertType }
//   - WAMessage wraps proto.IWebMessageInfo:
//       key.remoteJid  — chat JID (individual: <number>@s.whatsapp.net, group: <id>@g.us)
//       key.fromMe     — true when we sent the message
//       pushName       — display name of the sender
//       message.conversation | message.extendedTextMessage.text — plain text body
//   - DisconnectReason.loggedOut (401): do NOT reconnect; user must re-scan QR.
//   - On all other close events, recreate the socket (Baileys v7 pattern).

export class WhatsAppAdapter implements Adapter {
  readonly platform = "whatsapp";

  private readonly authDir: string;
  // Live socket; null before first connect or after permanent logout.
  private sock: ReturnType<typeof makeWASocket> | null = null;

  constructor(authDir = "./data/whatsapp") {
    this.authDir = authDir;
  }

  async start(onInbound: InboundHandler): Promise<void> {
    await this.connect(onInbound);
  }

  private async connect(onInbound: InboundHandler): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);

    const sock = makeWASocket({
      auth: state,
      // We render the QR ourselves so we can use qrcode-terminal
      printQRInTerminal: false,
    });

    this.sock = sock;
    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log("[whatsapp] Scan this QR code with WhatsApp → Linked Devices:");
        qrcodeTerminal.generate(qr, { small: true });
      }

      if (connection === "close") {
        // lastDisconnect.error is a Boom-style object; status lives in .output.statusCode
        const statusCode = (
          lastDisconnect?.error as { output?: { statusCode?: number } } | undefined
        )?.output?.statusCode;

        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        console.log(
          `[whatsapp] Connection closed (code=${statusCode}). Reconnect: ${shouldReconnect}`
        );

        if (shouldReconnect) {
          void this.connect(onInbound);
        } else {
          console.error(
            "[whatsapp] Logged out. Remove ./data/whatsapp and restart to re-scan QR."
          );
          this.sock = null;
        }
      } else if (connection === "open") {
        console.log("[whatsapp] Connected.");
      }
    });

    sock.ev.on("messages.upsert", async ({ messages, type }) => {
      // 'notify' = real-time inbound; 'append' = history sync — skip the latter
      if (type !== "notify") return;

      for (const msg of messages) {
        if (msg.key.fromMe) continue;

        const jid = msg.key.remoteJid;
        if (!jid) continue;

        // Extract text from the two most common plain-text message shapes
        const text =
          msg.message?.conversation ??
          msg.message?.extendedTextMessage?.text ??
          null;

        if (!text) continue; // media / sticker / etc. — skip silently

        const sender = msg.pushName ?? msg.key.participant ?? jid;

        try {
          await onInbound(jid, sender, text);
        } catch (err) {
          console.error("[whatsapp] onInbound error:", err);
        }
      }
    });
  }

  async sendMessage(recipient: string, text: string): Promise<void> {
    if (!this.sock) {
      throw new Error("[whatsapp] Not connected; cannot send message.");
    }
    // Recipient must be a valid WhatsApp JID:
    //   individual: "<countrycode><number>@s.whatsapp.net"
    //   group:      "<groupId>@g.us"
    await this.sock.sendMessage(recipient, { text });
  }
}
