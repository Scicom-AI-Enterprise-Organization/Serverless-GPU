// Returns the current session token to the *signed-in* user. Used by the
// API Keys page so the user can copy their bearer token for use in scripts.
// httpOnly cookie means the browser can't read it directly — this route is
// the safe server-side shim.

import { NextRequest, NextResponse } from "next/server";
import { TOKEN_COOKIE, USER_COOKIE } from "@/lib/auth-cookie";

export async function GET(req: NextRequest) {
  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  const username = req.cookies.get(USER_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }
  return NextResponse.json({ token, username: username ?? null });
}
