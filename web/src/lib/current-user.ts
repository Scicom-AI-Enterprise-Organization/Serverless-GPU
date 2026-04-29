import { cookies } from "next/headers";
import { USER_COOKIE } from "./auth-cookie";

// Reads the username we stashed at login time. Cheap — just reads a cookie,
// no gateway call. The middleware already enforces that an unauthenticated
// session can't reach app pages, so the cookie is always present here.
export async function currentUsername(): Promise<string> {
  const jar = await cookies();
  return jar.get(USER_COOKIE)?.value ?? "";
}
