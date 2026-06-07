import { describe, it, expect, vi, beforeEach } from "vitest";
import { parseSignalEnvelope } from "../src/adapters/signal.js";
import { SignalAdapter } from "../src/adapters/signal.js";

// ---------------------------------------------------------------------------
// parseSignalEnvelope — pure unit tests (no network)
// ---------------------------------------------------------------------------

describe("parseSignalEnvelope", () => {
  it("extracts chatId, sender, and text from a well-formed data message envelope", () => {
    /**
     * GIVEN a valid signal-cli-rest-api WebSocket envelope with sourceNumber and dataMessage
     * WHEN parseSignalEnvelope is called
     * THEN it returns the correct chatId, sender, and text
     */
    const envelope = {
      envelope: {
        source: "uuid-abc-123",
        sourceNumber: "+15551234567",
        dataMessage: {
          message: "Hello from Signal",
        },
      },
    };

    const result = parseSignalEnvelope(envelope);
    expect(result).not.toBeNull();
    expect(result!.chatId).toBe("+15551234567");
    expect(result!.sender).toBe("+15551234567");
    expect(result!.text).toBe("Hello from Signal");
  });

  it("falls back to source UUID when sourceNumber is absent", () => {
    /**
     * GIVEN a signal envelope that has source but no sourceNumber
     * WHEN parseSignalEnvelope is called
     * THEN it uses source as the sender/chatId
     */
    const envelope = {
      envelope: {
        source: "uuid-only",
        dataMessage: { message: "hi" },
      },
    };

    const result = parseSignalEnvelope(envelope);
    expect(result).not.toBeNull();
    expect(result!.sender).toBe("uuid-only");
    expect(result!.chatId).toBe("uuid-only");
  });

  it("returns null for a receipt/typing envelope (no dataMessage)", () => {
    /**
     * GIVEN a signal envelope without a dataMessage (e.g. a read receipt)
     * WHEN parseSignalEnvelope is called
     * THEN it returns null
     */
    const envelope = {
      envelope: {
        sourceNumber: "+15551234567",
        receiptMessage: { isRead: true },
      },
    };

    expect(parseSignalEnvelope(envelope)).toBeNull();
  });

  it("returns null when dataMessage.message is null", () => {
    /**
     * GIVEN a signal envelope where the dataMessage has a null message (attachment only)
     * WHEN parseSignalEnvelope is called
     * THEN it returns null
     */
    const envelope = {
      envelope: {
        sourceNumber: "+15551234567",
        dataMessage: { message: null },
      },
    };

    expect(parseSignalEnvelope(envelope)).toBeNull();
  });

  it("returns null when dataMessage.message is an empty string", () => {
    /**
     * GIVEN a signal envelope with an empty message text
     * WHEN parseSignalEnvelope is called
     * THEN it returns null
     */
    const envelope = {
      envelope: {
        sourceNumber: "+1555",
        dataMessage: { message: "  " },
      },
    };

    expect(parseSignalEnvelope(envelope)).toBeNull();
  });

  it("returns null when there is no sender identity", () => {
    /**
     * GIVEN a signal envelope with neither source nor sourceNumber
     * WHEN parseSignalEnvelope is called
     * THEN it returns null
     */
    const envelope = {
      envelope: {
        dataMessage: { message: "ghost" },
      },
    };

    expect(parseSignalEnvelope(envelope)).toBeNull();
  });

  it("returns null for non-object input", () => {
    /**
     * GIVEN invalid input (string, number, null)
     * WHEN parseSignalEnvelope is called
     * THEN it returns null each time
     */
    expect(parseSignalEnvelope(null)).toBeNull();
    expect(parseSignalEnvelope("not an object")).toBeNull();
    expect(parseSignalEnvelope(42)).toBeNull();
  });

  it("returns null when envelope wrapper is absent", () => {
    /**
     * GIVEN an object without an 'envelope' key
     * WHEN parseSignalEnvelope is called
     * THEN it returns null
     */
    expect(parseSignalEnvelope({ something: "else" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// SignalAdapter.sendMessage — HTTP request shape (injected fetch)
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

describe("SignalAdapter.sendMessage", () => {
  it("POSTs to /v2/send with the correct body shape", async () => {
    /**
     * GIVEN a SignalAdapter with a stubbed fetch
     * WHEN sendMessage is called
     * THEN it POSTs to {SIGNAL_API_URL}/v2/send with number, recipients, and message fields
     */
    const { fn, calls } = makeFetch(200, {});

    // Monkey-patch the global fetch for this test
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new SignalAdapter("http://localhost:8080", "+15551234567");
      await adapter.sendMessage("+19998887777", "Test Signal message");

      expect(calls).toHaveLength(1);
      const [url, init] = calls[0]!;
      expect(url).toBe("http://localhost:8080/v2/send");
      expect(init?.method).toBe("POST");

      const sent = JSON.parse(init?.body as string) as Record<string, unknown>;
      expect(sent).toMatchObject({
        message: "Test Signal message",
        number: "+15551234567",
        recipients: ["+19998887777"],
      });
    } finally {
      globalThis.fetch = original;
    }
  });

  it("strips trailing slash from apiUrl before building the send path", async () => {
    /**
     * GIVEN SIGNAL_API_URL with a trailing slash
     * WHEN sendMessage is called
     * THEN the URL is normalised (no double slash)
     */
    const { fn, calls } = makeFetch(200, {});
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new SignalAdapter("http://localhost:8080/", "+15550000000");
      await adapter.sendMessage("+1111", "hi");

      const [url] = calls[0]!;
      expect(url).toBe("http://localhost:8080/v2/send");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("throws when the server returns a non-2xx status", async () => {
    /**
     * GIVEN a stubbed fetch that returns 500
     * WHEN sendMessage is called
     * THEN it throws an error mentioning the status code
     */
    const { fn } = makeFetch(500, { error: "internal error" });
    const original = globalThis.fetch;
    globalThis.fetch = fn;

    try {
      const adapter = new SignalAdapter("http://localhost:8080", "+1555");
      await expect(adapter.sendMessage("+1999", "boom")).rejects.toThrow("500");
    } finally {
      globalThis.fetch = original;
    }
  });
});
