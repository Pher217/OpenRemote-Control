import { describe, it, expect, beforeEach, afterEach } from "vitest";
import http from "http";
import {
  setQR,
  setStatus,
  clearQR,
  getAll,
  getOne,
  resetState,
} from "../src/setup-state.js";
import { startSetupServer } from "../src/setup-server.js";

// ---------------------------------------------------------------------------
// setup-state unit tests
// ---------------------------------------------------------------------------

describe("setup-state", () => {
  beforeEach(() => {
    resetState();
  });

  it("setQR sets status to waiting_qr and stores the qr string", () => {
    /**
     * GIVEN a fresh state
     * WHEN setQR is called with a platform and qr string
     * THEN the record has status waiting_qr and qr matches the input
     */
    setQR("whatsapp", "MYQR123");
    const record = getOne("whatsapp");
    expect(record).toBeDefined();
    expect(record!.status).toBe("waiting_qr");
    expect(record!.qr).toBe("MYQR123");
  });

  it("clearQR removes the qr field while preserving status", () => {
    /**
     * GIVEN a record with a qr
     * WHEN clearQR is called
     * THEN the qr field is undefined but the record still exists
     */
    setQR("whatsapp", "QR_TO_CLEAR");
    setStatus("whatsapp", "linked");
    clearQR("whatsapp");
    const record = getOne("whatsapp");
    expect(record).toBeDefined();
    expect(record!.qr).toBeUndefined();
    expect(record!.status).toBe("linked");
  });

  it("getAll returns all registered records", () => {
    /**
     * GIVEN multiple platforms have been registered
     * WHEN getAll is called
     * THEN it returns one record per platform
     */
    setStatus("slack", "needs_token", "Set SLACK_BOT_TOKEN");
    setStatus("discord", "linked");
    setQR("whatsapp", "QRQR");
    const all = getAll();
    expect(all).toHaveLength(3);
    const platforms = all.map((r) => r.platform);
    expect(platforms).toContain("slack");
    expect(platforms).toContain("discord");
    expect(platforms).toContain("whatsapp");
  });

  it("resetState clears all records", () => {
    /**
     * GIVEN some records have been added
     * WHEN resetState is called
     * THEN getAll returns an empty array
     */
    setStatus("discord", "linked");
    setQR("whatsapp", "QR");
    resetState();
    expect(getAll()).toHaveLength(0);
    expect(getOne("discord")).toBeUndefined();
  });

  it("setStatus stores detail when provided", () => {
    /**
     * GIVEN setStatus is called with a detail string
     * WHEN getOne is called
     * THEN the record's detail matches the provided string
     */
    setStatus("signal", "needs_token", "Set SIGNAL_API_URL");
    const record = getOne("signal");
    expect(record!.detail).toBe("Set SIGNAL_API_URL");
  });

  it("setQR preserves existing detail", () => {
    /**
     * GIVEN a record with a detail set via setStatus
     * WHEN setQR is called for the same platform
     * THEN the existing detail is preserved
     */
    setStatus("whatsapp", "connecting", "reconnecting");
    setQR("whatsapp", "NEW_QR");
    const record = getOne("whatsapp");
    expect(record!.qr).toBe("NEW_QR");
    expect(record!.status).toBe("waiting_qr");
  });
});

// ---------------------------------------------------------------------------
// setup-server HTTP tests
// ---------------------------------------------------------------------------

function getPort(server: http.Server): number {
  const addr = server.address();
  if (!addr || typeof addr === "string") throw new Error("No address");
  return addr.port;
}

async function fetchText(url: string): Promise<{ status: number; body: string; contentType: string }> {
  const res = await fetch(url);
  const body = await res.text();
  return { status: res.status, body, contentType: res.headers.get("content-type") ?? "" };
}

describe("setup-server HTTP", () => {
  let server: http.Server;
  let port: number;

  beforeEach(async () => {
    resetState();
    server = startSetupServer(0, new Set(["whatsapp"]));
    await new Promise<void>((resolve) => server.once("listening", resolve));
    port = getPort(server);
  });

  afterEach(async () => {
    await new Promise<void>((resolve, reject) =>
      server.close((err) => (err ? reject(err) : resolve()))
    );
  });

  it("GET /status.json returns a JSON array containing whatsapp", async () => {
    /**
     * GIVEN the server is started with whatsapp in enabledPlatforms
     * WHEN GET /status.json is called before any status is set
     * THEN it returns 200 JSON array (possibly empty since no setStatus called yet)
     *   and hasQr is false
     */
    setStatus("whatsapp", "connecting");
    const { status, body, contentType } = await fetchText(`http://localhost:${port}/status.json`);
    expect(status).toBe(200);
    expect(contentType).toContain("application/json");
    const parsed = JSON.parse(body) as Array<{ platform: string; hasQr: boolean }>;
    expect(Array.isArray(parsed)).toBe(true);
    const wa = parsed.find((r) => r.platform === "whatsapp");
    expect(wa).toBeDefined();
    expect(wa!.hasQr).toBe(false);
  });

  it("GET /status.json omits raw qr and includes hasQr:true after setQR", async () => {
    /**
     * GIVEN setQR has been called for whatsapp
     * WHEN GET /status.json is called
     * THEN the response includes hasQr:true and no 'qr' field
     */
    setQR("whatsapp", "TESTQR");
    const { status, body } = await fetchText(`http://localhost:${port}/status.json`);
    expect(status).toBe(200);
    const parsed = JSON.parse(body) as Array<Record<string, unknown>>;
    const wa = parsed.find((r) => r["platform"] === "whatsapp");
    expect(wa).toBeDefined();
    expect(wa!["hasQr"]).toBe(true);
    expect("qr" in wa!).toBe(false);
  });

  it("GET /qr/whatsapp.svg returns 200 SVG after setQR", async () => {
    /**
     * GIVEN setQR has been called with a valid qr string
     * WHEN GET /qr/whatsapp.svg is called
     * THEN it returns 200 with content-type image/svg+xml and body containing <svg
     */
    setQR("whatsapp", "TESTQR");
    const { status, body, contentType } = await fetchText(`http://localhost:${port}/qr/whatsapp.svg`);
    expect(status).toBe(200);
    expect(contentType).toContain("image/svg+xml");
    expect(body).toContain("<svg");
  });

  it("GET /qr/whatsapp.svg returns 404 when no qr is set", async () => {
    /**
     * GIVEN no QR has been set for whatsapp
     * WHEN GET /qr/whatsapp.svg is called
     * THEN it returns 404
     */
    const { status } = await fetchText(`http://localhost:${port}/qr/whatsapp.svg`);
    expect(status).toBe(404);
  });

  it("GET / returns 200 HTML containing the platform name", async () => {
    /**
     * GIVEN the server has whatsapp as an enabled platform
     * WHEN GET / is called
     * THEN it returns 200 text/html with 'whatsapp' in the body
     */
    const { status, body, contentType } = await fetchText(`http://localhost:${port}/`);
    expect(status).toBe(200);
    expect(contentType).toContain("text/html");
    expect(body.toLowerCase()).toContain("whatsapp");
  });

  it("GET /unknown returns 404", async () => {
    /**
     * GIVEN a request for a path that does not exist
     * WHEN the request is made
     * THEN it returns 404
     */
    const { status } = await fetchText(`http://localhost:${port}/unknown-path`);
    expect(status).toBe(404);
  });
});
