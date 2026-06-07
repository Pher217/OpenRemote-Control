// SECURITY: This page exposes QR codes that link the operator's messaging accounts.
// Bind only to 127.0.0.1 in production. If running in Docker, map the host port
// to 127.0.0.1 only (e.g. "127.0.0.1:8088:8088") — never expose it publicly.

import http from "http";
import QRCode from "qrcode";
import { getAll, getOne } from "./setup-state.js";
import type { PlatformRecord } from "./setup-state.js";

function statusBadge(record: PlatformRecord): string {
  switch (record.status) {
    case "waiting_qr":
      return '<span class="badge waiting">Waiting for QR scan</span>';
    case "connecting":
      return '<span class="badge connecting">Connecting…</span>';
    case "linked":
      return '<span class="badge linked">✅ Connected</span>';
    case "needs_token":
      return '<span class="badge needs-token">Token required</span>';
    case "error":
      return '<span class="badge error">⚠️ Error</span>';
  }
}

function platformCard(record: PlatformRecord): string {
  const badge = statusBadge(record);
  const detail = record.detail
    ? `<p class="detail">${escapeHtml(record.detail)}</p>`
    : "";

  let qrSection = "";
  if (record.qr) {
    qrSection = `
      <img src="/qr/${encodeURIComponent(record.platform)}.svg" alt="QR code — scan with ${escapeHtml(record.platform)}" width="240" height="240">
      <p class="instruction">Open WhatsApp → Linked Devices → Link a Device, then scan.</p>`;
  }

  if (record.status === "needs_token") {
    qrSection = `<p class="instruction">${detail ? "" : "Set the required environment variable and restart."}</p>`;
  }

  return `
    <div class="card">
      <h2>${escapeHtml(record.platform)}</h2>
      ${badge}
      ${detail && record.status !== "needs_token" ? detail : ""}
      ${record.status === "needs_token" ? `<p class="instruction">${record.detail ? escapeHtml(record.detail) : "Set the required environment variable and restart."}</p>` : qrSection}
    </div>`;
}

function buildIndexHtml(enabledPlatforms: Set<string>): string {
  const records = getAll();
  const platformMap = new Map(records.map((r) => [r.platform, r]));

  const cards = Array.from(enabledPlatforms)
    .map((p) => {
      const record = platformMap.get(p) ?? {
        platform: p,
        status: "connecting" as const,
        updatedAt: Date.now(),
      };
      return platformCard(record);
    })
    .join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="3">
  <title>OpenRemote-Control — Connect your messaging app</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; padding: 2rem; }
    h1 { font-size: 1.4rem; margin-bottom: 1.5rem; color: #111; }
    .cards { display: flex; flex-wrap: wrap; gap: 1.5rem; }
    .card { background: #fff; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 4px rgba(0,0,0,.1); min-width: 260px; max-width: 340px; display: flex; flex-direction: column; align-items: flex-start; gap: .75rem; }
    .card h2 { font-size: 1.1rem; text-transform: capitalize; }
    .badge { font-size: .8rem; font-weight: 600; padding: .2rem .6rem; border-radius: 999px; }
    .badge.waiting { background: #fff3cd; color: #856404; }
    .badge.connecting { background: #cfe2ff; color: #084298; }
    .badge.linked { background: #d1e7dd; color: #0a3622; }
    .badge.needs-token { background: #f8d7da; color: #58151c; }
    .badge.error { background: #f8d7da; color: #58151c; }
    .card img { border: 1px solid #ddd; border-radius: 4px; }
    .instruction { font-size: .85rem; color: #555; }
    .detail { font-size: .8rem; color: #777; font-family: monospace; word-break: break-all; }
    footer { margin-top: 2rem; font-size: .75rem; color: #999; }
  </style>
</head>
<body>
  <h1>OpenRemote-Control — Connect your messaging app</h1>
  <div class="cards">
    ${cards}
  </div>
  <footer>Auto-refreshes every 3 seconds. Keep this page on your local network only.</footer>
</body>
</html>`;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildStatusJson(): string {
  const records = getAll().map(({ qr, ...rest }) => ({
    ...rest,
    hasQr: Boolean(qr),
  }));
  return JSON.stringify(records);
}

export function startSetupServer(
  port: number,
  enabledPlatforms: Set<string>
): http.Server {
  const server = http.createServer((req, res) => {
    const url = req.url ?? "/";

    if (url === "/" || url === "") {
      const html = buildIndexHtml(enabledPlatforms);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
      return;
    }

    if (url === "/status.json") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(buildStatusJson());
      return;
    }

    const qrMatch = url.match(/^\/qr\/([^/]+)\.svg$/);
    if (qrMatch) {
      const platform = decodeURIComponent(qrMatch[1]!);
      const record = getOne(platform);
      if (!record?.qr) {
        res.writeHead(404, { "Content-Type": "text/plain" });
        res.end("No QR available");
        return;
      }
      void QRCode.toString(record.qr, { type: "svg", margin: 1, width: 320 }).then(
        (svg) => {
          res.writeHead(200, { "Content-Type": "image/svg+xml" });
          res.end(svg);
        },
        (err: unknown) => {
          console.error("[setup-server] QR render error:", err);
          res.writeHead(500, { "Content-Type": "text/plain" });
          res.end("QR render error");
        }
      );
      return;
    }

    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("Not found");
  });

  server.listen(port, "0.0.0.0");
  return server;
}
