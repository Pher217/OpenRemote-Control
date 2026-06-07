export type PlatformStatus =
  | "waiting_qr"
  | "connecting"
  | "linked"
  | "needs_token"
  | "error";

export interface PlatformRecord {
  platform: string;
  status: PlatformStatus;
  qr?: string;
  detail?: string;
  updatedAt: number;
}

const state = new Map<string, PlatformRecord>();

export function setStatus(
  platform: string,
  status: PlatformStatus,
  detail?: string
): void {
  const existing = state.get(platform);
  state.set(platform, {
    platform,
    status,
    qr: existing?.qr,
    detail,
    updatedAt: Date.now(),
  });
}

export function setQR(platform: string, qr: string): void {
  const existing = state.get(platform);
  state.set(platform, {
    platform,
    status: "waiting_qr",
    qr,
    detail: existing?.detail,
    updatedAt: Date.now(),
  });
}

export function clearQR(platform: string): void {
  const existing = state.get(platform);
  if (!existing) return;
  state.set(platform, {
    ...existing,
    qr: undefined,
    updatedAt: Date.now(),
  });
}

export function getAll(): PlatformRecord[] {
  return Array.from(state.values());
}

export function getOne(platform: string): PlatformRecord | undefined {
  return state.get(platform);
}

export function resetState(): void {
  state.clear();
}
