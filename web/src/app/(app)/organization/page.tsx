import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth-cookie";
import { getMe } from "@/lib/me";
import { ConsoleTopbar } from "@/components/console/topbar";
import { OrganizationTable, type OrgUser } from "./table";

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

async function loadUsers(token: string): Promise<OrgUser[]> {
  const r = await fetch(`${GATEWAY}/admin/users`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!r.ok) return [];
  return (await r.json()) as OrgUser[];
}

export default async function OrganizationPage() {
  const me = await getMe();
  if (!me) redirect("/login");
  if (me.role !== "admin") redirect("/serverless");

  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value ?? "";
  const users = await loadUsers(token);

  return (
    <div className="flex min-h-full flex-col">
      <ConsoleTopbar crumbs={[{ label: "Organization" }]} username={me.username} />
      <div className="mx-auto w-full max-w-6xl px-6 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Organization</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage members and role assignments. Admins manage roles; developers
            can use Serverless and the Hub; users have no platform access until
            promoted.
          </p>
        </header>

        <section className="rounded-xl border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-5 py-4">
            <div>
              <h2 className="text-base font-semibold">Members ({users.length})</h2>
              <p className="text-xs text-muted-foreground">All users with access to this gateway.</p>
            </div>
          </div>
          <OrganizationTable users={users} currentUserId={me.user_id} />
        </section>
      </div>
    </div>
  );
}
