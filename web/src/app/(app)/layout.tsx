import { ConsoleSidebar } from "@/components/console/sidebar";
import { SidebarStateProvider } from "@/components/console/sidebar-state";
import { getMe } from "@/lib/me";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  const isAdmin = me?.role === "admin";
  // If me is null (no session) we still render the full sidebar — the per-page
  // NoAccessAlert handles the actual gating. But when we *do* have a me, hide
  // sections this user has no access to so the nav reflects reality.
  const sections = me?.sections ?? {
    inference: true,
    benchmark: true,
    compute: true,
  };
  return (
    <SidebarStateProvider>
      <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
        <div className="flex min-h-0 flex-1">
          <ConsoleSidebar isAdmin={isAdmin} sections={sections} />
          <main className="min-w-0 flex-1 overflow-y-auto scrollbar-thin">
            {children}
          </main>
        </div>
      </div>
    </SidebarStateProvider>
  );
}
