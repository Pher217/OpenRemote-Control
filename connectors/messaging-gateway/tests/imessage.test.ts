import { describe, it, expect, vi } from "vitest";
import { parseBlueBubblesEvent } from "../src/adapters/imessage.js";
import { IMessageAdapter } from "../src/adapters/imessage.js";

// ---------------------------------------------------------------------------
// parseBlueBubblesEvent — pure unit tests (no network)
// ---------------------------------------------------------------------------

describe("parseBlueBubblesEvent", () => {
  it("extracts chatId, sender, and text from a well-formed new-message event", () => {
    /**
     * GIVEN a BlueBubbles new-message webhook payload with all expected fields
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns the correct chatId (chat GUID), sender (handle address), and text
     */
    const payload = {
      type: "new-message",
      data: {
        text: "Hey from iMessage",
        isFromMe: false,
        handle: { address: "+15551234567" },
        chats: [{ guid: "iMessage;-;+15551234567" }],
      },
    };

    const result = parseBlueBubblesEvent(payload);
    expect(result).not.toBeNull();
    expect(result!.chatId).toBe("iMessage;-;+15551234567");
    expect(result!.sender).toBe("+15551234567");
    expect(result!.text).toBe("Hey from iMessage");
  });

  it("uses handle address as chatId when chats array is empty", () => {
    /**
     * GIVEN a BlueBubbles payload where chats is an empty array
     * WHEN parseBlueBubblesEvent is called
     * THEN it falls back to the handle address for chatId
     */
    const payload = {
      type: "new-message",
      data: {
        text: "fallback chat",
        isFromMe: false,
        handle: { address: "+19998887777" },
        chats: [],
      },
    };

    const result = parseBlueBubblesEvent(payload);
    expect(result).not.toBeNull();
    expect(result!.chatId).toBe("+19998887777");
    expect(result!.sender).toBe("+19998887777");
  });

  it("returns null for messages sent by us (isFromMe = true)", () => {
    /**
     * GIVEN a new-message payload where isFromMe is true
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null (avoid echo loops)
     */
    const payload = {
      type: "new-message",
      data: {
        text: "my own message",
        isFromMe: true,
        handle: { address: "+15559999999" },
        chats: [{ guid: "iMessage;-;+15559999999" }],
      },
    };

    expect(parseBlueBubblesEvent(payload)).toBeNull();
  });

  it("returns null for non-message event types", () => {
    /**
     * GIVEN a BlueBubbles webhook payload with type 'message-updated'
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null
     */
    const payload = {
      type: "message-updated",
      data: {
        text: "edited text",
        isFromMe: false,
        handle: { address: "+1555" },
        chats: [],
      },
    };

    expect(parseBlueBubblesEvent(payload)).toBeNull();
  });

  it("returns null when text is null or empty", () => {
    /**
     * GIVEN a new-message payload with null or empty text (attachment only)
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null
     */
    const nullText = {
      type: "new-message",
      data: { text: null, isFromMe: false, handle: { address: "+1555" }, chats: [] },
    };
    const emptyText = {
      type: "new-message",
      data: { text: "  ", isFromMe: false, handle: { address: "+1555" }, chats: [] },
    };

    expect(parseBlueBubblesEvent(nullText)).toBeNull();
    expect(parseBlueBubblesEvent(emptyText)).toBeNull();
  });

  it("returns null when handle address is missing", () => {
    /**
     * GIVEN a new-message payload where handle is null
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null
     */
    const payload = {
      type: "new-message",
      data: {
        text: "who am I",
        isFromMe: false,
        handle: null,
        chats: [{ guid: "iMessage;-;ghost" }],
      },
    };

    expect(parseBlueBubblesEvent(payload)).toBeNull();
  });

  it("returns null for non-object inputs", () => {
    /**
     * GIVEN non-object inputs (null, string, number)
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null each time
     */
    expect(parseBlueBubblesEvent(null)).toBeNull();
    expect(parseBlueBubblesEvent("bad")).toBeNull();
    expect(parseBlueBubblesEvent(123)).toBeNull();
  });

  it("returns null when data is absent", () => {
    /**
     * GIVEN a new-message payload without a data field
     * WHEN parseBlueBubblesEvent is called
     * THEN it returns null
     */
    expect(parseBlueBubblesEvent({ type: "new-message" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// IMessageAdapter.sendMessage — HTTP request shape (injected fetch)
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

describe("IMessageAdapter.sendMessage", () => {
  it("POSTs to /api/v1/message/text with chatGuid when recipient contains semicolons", async () => {
    /**
     * GIVEN an IMessageAdapter with a stubbed fetch and a chat-GUID-style recipient
     * WHEN sendMessage is called
     * THEN it POSTs to {BLUEBUBBLES_URL}/api/v1/message/text with chatGuid, message, method
     */
    const { fn, calls } = makeFetch(200, { status: 200 });
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new IMessageAdapter("http://mac.local:1234", "s3cr3t");
      await adapter.sendMessage("iMessage;-;+15551234567", "Hello from gateway");

      expect(calls).toHaveLength(1);
      const [url, init] = calls[0]!;
      expect(url as string).toContain("/api/v1/message/text");
      expect(url as string).toContain("password=s3cr3t");

      const sent = JSON.parse(init?.body as string) as Record<string, unknown>;
      expect(sent).toMatchObject({
        chatGuid: "iMessage;-;+15551234567",
        message: "Hello from gateway",
        method: "apple-script",
      });
    } finally {
      globalThis.fetch = original;
    }
  });

  it("POSTs with address field when recipient has no semicolons (bare handle)", async () => {
    /**
     * GIVEN an IMessageAdapter and a bare E.164 address as the recipient
     * WHEN sendMessage is called
     * THEN the request body uses 'address' rather than 'chatGuid'
     */
    const { fn, calls } = makeFetch(200, {});
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new IMessageAdapter("http://mac.local:1234", "pw");
      await adapter.sendMessage("+19998887777", "bare handle message");

      const [, init] = calls[0]!;
      const sent = JSON.parse(init?.body as string) as Record<string, unknown>;
      expect(sent).toMatchObject({
        address: "+19998887777",
        message: "bare handle message",
        method: "apple-script",
      });
      expect(sent).not.toHaveProperty("chatGuid");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("URL-encodes the password in the query string", async () => {
    /**
     * GIVEN a BlueBubbles password containing special characters
     * WHEN sendMessage is called
     * THEN the password is URL-encoded in the request URL
     */
    const { fn, calls } = makeFetch(200, {});
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new IMessageAdapter("http://mac.local:1234", "p@ss w0rd!");
      await adapter.sendMessage("+1555", "hi");

      const [url] = calls[0]!;
      expect(url as string).toContain("password=p%40ss%20w0rd!");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("throws when the server returns a non-2xx status", async () => {
    /**
     * GIVEN a stubbed fetch that returns 403
     * WHEN sendMessage is called
     * THEN it throws an error mentioning the status code
     */
    const { fn } = makeFetch(403, { error: "forbidden" });
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new IMessageAdapter("http://mac.local:1234", "bad-pw");
      await expect(adapter.sendMessage("+1555", "test")).rejects.toThrow("403");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("strips trailing slash from BLUEBUBBLES_URL", async () => {
    /**
     * GIVEN BLUEBUBBLES_URL with a trailing slash
     * WHEN sendMessage is called
     * THEN the URL does not contain a double slash
     */
    const { fn, calls } = makeFetch(200, {});
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new IMessageAdapter("http://mac.local:1234/", "pw");
      await adapter.sendMessage("+1555", "hi");

      const [url] = calls[0]!;
      expect(url as string).not.toContain("//api");
      expect(url as string).toContain("http://mac.local:1234/api/v1/message/text");
    } finally {
      globalThis.fetch = original;
    }
  });
});
