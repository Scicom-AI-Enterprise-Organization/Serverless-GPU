import { NextRequest, NextResponse } from "next/server";
import { clearAuthCookies, TOKEN_COOKIE } from "@/lib/auth-cookie";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

export async function POST(req: NextRequest) {
  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  if (token) {
    // Best-effort revoke on the gateway. We always clear the cookie even if
    // the gateway call fails (e.g. token already expired).
    try {
      await fetch(`${GATEWAY}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      // ignore
    }
  }
  const res = NextResponse.json({ ok: true });
  clearAuthCookies(res.cookies);
  return res;
}
