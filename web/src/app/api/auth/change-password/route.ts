import { NextRequest, NextResponse } from "next/server";
import { TOKEN_COOKIE, clearAuthCookies } from "@/lib/auth-cookie";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

export async function POST(req: NextRequest) {
  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const body = await req.json().catch(() => ({}));
  const r = await fetch(`${GATEWAY}/auth/change-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await r.text();
  const res = new NextResponse(text, {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
  // Gateway revokes the session on success — drop our cookies too so the next
  // navigation bounces through /login.
  if (r.ok) clearAuthCookies(res.cookies);
  return res;
}
