"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { Boxes, KeyRound, Layers, Users } from "lucide-react";
import { cn } from "@/lib/utils";

type Item = { label: string; href: string; icon: React.ElementType };

const RESOURCES: Item[] = [
  { label: "Serverless", href: "/serverless", icon: Boxes },
  { label: "All workers", href: "/serverless/new", icon: Layers },
];
const ACCOUNT: Item[] = [{ label: "API keys", href: "/api-keys", icon: KeyRound }];
const ADMIN: Item[] = [{ label: "Organization", href: "/organization", icon: Users }];

export function ConsoleSidebar({ isAdmin = false }: { isAdmin?: boolean } = {}) {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href !== "/serverless") return pathname === href;
    return pathname === "/serverless" || /^\/serverless\/(?!new$)/.test(pathname);
  };

  return (
    <aside className="hidden h-full w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
      <Link
        href="/"
        className="flex h-14 items-center gap-2 border-b border-sidebar-border px-4 hover:bg-sidebar-accent/40"
      >
        <Image
          src="/logos/scicom-logo.png"
          alt="Scicom"
          width={96}
          height={24}
          priority
          className="h-6 w-auto select-none"
        />
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Serverless
        </span>
      </Link>

      <nav className="flex-1 overflow-y-auto py-3 scrollbar-thin">
        <SidebarGroup label="Resources">
          {RESOURCES.map((item) => (
            <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
          ))}
        </SidebarGroup>

        <SidebarGroup label="Account">
          {ACCOUNT.map((item) => (
            <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
          ))}
        </SidebarGroup>

        {isAdmin && (
          <SidebarGroup label="Admin">
            {ADMIN.map((item) => (
              <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
            ))}
          </SidebarGroup>
        )}
      </nav>
    </aside>
  );
}

function SidebarGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <div className="mt-3 flex w-full items-center px-4 py-1.5 text-xs font-medium text-muted-foreground">
        {label}
      </div>
      <ul className="space-y-px px-2">{children}</ul>
    </>
  );
}

function SidebarItem({ item, active }: { item: Item; active?: boolean }) {
  return (
    <li>
      <Link
        href={item.href}
        className={cn(
          "group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
          active
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
        )}
      >
        <item.icon className="h-4 w-4 shrink-0" />
        <span className="flex-1 truncate">{item.label}</span>
      </Link>
    </li>
  );
}
