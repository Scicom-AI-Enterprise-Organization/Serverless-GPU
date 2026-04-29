// Shared cookie helpers. Two cookies in play:
//
//   sgpu_token  - httpOnly bearer token. Server-only. Forwarded to the
//                 gateway as `Authorization: Bearer <token>`.
//   sgpu_user   - non-httpOnly username. Read by the client to show "logged
//                 in as" without an extra /auth/me round trip.
//
// Both share a 7d max-age (matches gateway session TTL).

import type { ResponseCookies } from "next/dist/server/web/spec-extension/cookies";

export const TOKEN_COOKIE = "sgpu_token";
export const USER_COOKIE = "sgpu_user";
export const SESSION_MAX_AGE_S = 7 * 24 * 3600;

export function setAuthCookies(
  jar: ResponseCookies,
  token: string,
  username: string,
) {
  const isProd = process.env.NODE_ENV === "production";
  jar.set(TOKEN_COOKIE, token, {
    httpOnly: true,
    secure: isProd,
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
  jar.set(USER_COOKIE, username, {
    httpOnly: false,
    secure: isProd,
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  });
}

export function clearAuthCookies(jar: ResponseCookies) {
  jar.delete(TOKEN_COOKIE);
  jar.delete(USER_COOKIE);
}
