export interface OutMsg {
  id: string;
  platform: string;
  recipient: string;
  text: string;
}

type FetchFn = typeof fetch;

export class BackendClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly fetchFn: FetchFn;

  constructor(baseUrl: string, token: string, fetchFn: FetchFn = fetch) {
    this.baseUrl = baseUrl;
    this.token = token;
    this.fetchFn = fetchFn;
  }

  async pollOutbox(platform: string): Promise<OutMsg[]> {
    const url = `${this.baseUrl}/api/gateway/outbox?platform=${encodeURIComponent(platform)}&max=20`;
    const res = await this.fetchFn(url, {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!res.ok) {
      throw new Error(`pollOutbox failed: ${res.status} ${res.statusText}`);
    }
    const body = (await res.json()) as { messages: OutMsg[] };
    return body.messages;
  }

  async postInbound(
    platform: string,
    chatId: string,
    sender: string,
    text: string
  ): Promise<string | null> {
    const res = await this.fetchFn(`${this.baseUrl}/api/gateway/inbound`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ platform, chat_id: chatId, sender, text }),
    });
    if (!res.ok) {
      throw new Error(`postInbound failed: ${res.status} ${res.statusText}`);
    }
    const body = (await res.json()) as { reply: string | null };
    return body.reply ?? null;
  }
}
