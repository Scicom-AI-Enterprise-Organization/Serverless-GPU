// Server-side helper: ask the gateway who the caller is, including role.
// Used by the (app) layout to decide whether to render admin-only nav.

import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "./auth-cookie";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

export type Me = {
  user_id: number;
  username: string;
  email?: string | null;
  is_admin: boolean;
  role: "user" | "developer" | "admin";
};

export async function getMe(): Promise<Me | null> {
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  if (!token) return null;
  try {
    const r = await fetch(`${GATEWAY}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!r.ok) return null;
    return (await r.json()) as Me;
  } catch {
    return null;
  }
}
