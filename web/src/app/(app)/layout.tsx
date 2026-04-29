import { ConsoleSidebar } from "@/components/console/sidebar";
import { getMe } from "@/lib/me";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  const isAdmin = me?.role === "admin";
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <div className="flex min-h-0 flex-1">
        <ConsoleSidebar isAdmin={isAdmin} />
        <main className="min-w-0 flex-1 overflow-y-auto scrollbar-thin">
          {children}
        </main>
      </div>
    </div>
  );
}
