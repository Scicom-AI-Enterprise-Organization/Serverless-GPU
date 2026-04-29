// Thin client for the serverless-gpu FastAPI gateway.
//
// On the server: read the session cookie and attach it to gateway requests.
// In the browser: route through /api/proxy/* — the proxy does the cookie →
// Bearer-token translation server-side, so the token never hits the bundle.

import type { AppRecord, CreateAppRequest, CreateAppResponse } from "./types";

const PUBLIC_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";
const isServer = typeof window === "undefined";

async function authHeaders(): Promise<Record<string, string>> {
  if (!isServer) return {};
  // Lazy import keeps `next/headers` out of the client bundle.
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  const token = jar.get("sgpu_token")?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = isServer ? `${PUBLIC_BASE}${path}` : `/api/proxy${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(await authHeaders()),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(url, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`gateway ${res.status}: ${body || res.statusText}`);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const gateway = {
  baseUrl: PUBLIC_BASE,
  listApps: () => request<AppRecord[]>("/apps"),
  getApp: (id: string) => request<AppRecord>(`/apps/${encodeURIComponent(id)}`),
  createApp: (body: CreateAppRequest) =>
    request<CreateAppResponse>("/apps", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteApp: (id: string) =>
    request<{ ok: boolean; app_id: string; drained_workers: number }>(
      `/apps/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
};
