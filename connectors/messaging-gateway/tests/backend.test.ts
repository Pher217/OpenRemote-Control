import { describe, it, expect, vi } from "vitest";
import { BackendClient } from "../src/backend.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type FetchArgs = [string | URL | Request, RequestInit | undefined];

function makeFetch(
  status: number,
  body: unknown
): { fn: typeof fetch; calls: FetchArgs[] } {
  const calls: FetchArgs[] = [];
  const fn = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    calls.push([input as string, init]);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
  return { fn, calls };
}

// ---------------------------------------------------------------------------
// pollOutbox
// ---------------------------------------------------------------------------

describe("BackendClient.pollOutbox", () => {
  it("sends a GET to /api/gateway/outbox with bearer token and platform query", async () => {
    const { fn, calls } = makeFetch(200, {
      messages: [
        { id: "1", platform: "slack", recipient: "C123", text: "hello" },
      ],
    });

    const client = new BackendClient("https://backend.example.com", "tok-secret", fn);
    const messages = await client.pollOutbox("slack");

    expect(calls).toHaveLength(1);
    const [url, init] = calls[0]!;
    expect(url).toBe(
      "https://backend.example.com/api/gateway/outbox?platform=slack&max=20"
    );
    expect((init?.headers as Record<string, string>)?.["Authorization"]).toBe(
      "Bearer tok-secret"
    );

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "1",
      platform: "slack",
      recipient: "C123",
      text: "hello",
    });
  });

  it("URL-encodes the platform name", async () => {
    const { fn, calls } = makeFetch(200, { messages: [] });
    const client = new BackendClient("https://backend.example.com", "t", fn);
    await client.pollOutbox("what sapp");

    const [url] = calls[0]!;
    expect(url).toContain("platform=what%20sapp");
  });

  it("returns an empty array when messages is empty", async () => {
    const { fn } = makeFetch(200, { messages: [] });
    const client = new BackendClient("https://backend.example.com", "t", fn);
    const result = await client.pollOutbox("discord");
    expect(result).toEqual([]);
  });

  it("throws on non-200 response", async () => {
    const { fn } = makeFetch(503, { detail: "unavailable" });
    const client = new BackendClient("https://backend.example.com", "t", fn);
    await expect(client.pollOutbox("slack")).rejects.toThrow("503");
  });
});

// ---------------------------------------------------------------------------
// postInbound
// ---------------------------------------------------------------------------

describe("BackendClient.postInbound", () => {
  it("POSTs the correct body and bearer token", async () => {
    const { fn, calls } = makeFetch(200, { reply: "pong" });
    const client = new BackendClient("https://backend.example.com", "tok-secret", fn);

    const reply = await client.postInbound("whatsapp", "49123@s.whatsapp.net", "Alice", "ping");

    expect(calls).toHaveLength(1);
    const [url, init] = calls[0]!;
    expect(url).toBe("https://backend.example.com/api/gateway/inbound");
    expect(init?.method).toBe("POST");

    const headers = init?.headers as Record<string, string>;
    expect(headers?.["Authorization"]).toBe("Bearer tok-secret");
    expect(headers?.["Content-Type"]).toBe("application/json");

    const sent = JSON.parse(init?.body as string) as Record<string, string>;
    expect(sent).toMatchObject({
      platform: "whatsapp",
      chat_id: "49123@s.whatsapp.net",
      sender: "Alice",
      text: "ping",
    });

    expect(reply).toBe("pong");
  });

  it("returns null when backend reply is null", async () => {
    const { fn } = makeFetch(200, { reply: null });
    const client = new BackendClient("https://backend.example.com", "t", fn);
    const reply = await client.postInbound("discord", "ch1", "Bob", "hi");
    expect(reply).toBeNull();
  });

  it("returns null when reply field is absent", async () => {
    const { fn } = makeFetch(200, {});
    const client = new BackendClient("https://backend.example.com", "t", fn);
    const reply = await client.postInbound("slack", "ch2", "Carol", "hey");
    expect(reply).toBeNull();
  });

  it("throws on non-200 response", async () => {
    const { fn } = makeFetch(401, { detail: "unauthorized" });
    const client = new BackendClient("https://backend.example.com", "bad-token", fn);
    await expect(
      client.postInbound("slack", "ch", "user", "msg")
    ).rejects.toThrow("401");
  });
});
