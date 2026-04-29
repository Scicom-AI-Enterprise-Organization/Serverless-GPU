"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { Boxes, ChevronDown, KeyRound, Layers } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

type Item = { label: string; href: string; icon: React.ElementType };

const HUB: Item[] = [{ label: "All workers", href: "/serverless/new", icon: Layers }];
const RESOURCES: Item[] = [{ label: "Serverless", href: "/serverless", icon: Boxes }];
const ACCOUNT: Item[] = [{ label: "API keys", href: "/api-keys", icon: KeyRound }];

export function ConsoleSidebar() {
  const pathname = usePathname();
  const [hubOpen, setHubOpen] = useState(true);
  const [resOpen, setResOpen] = useState(true);
  const [accountOpen, setAccountOpen] = useState(true);

  const isActive = (href: string) => {
    // Exact match for non-Serverless items (API keys, All workers).
    if (href !== "/serverless") return pathname === href;
    // "Serverless" lights up on the list and any endpoint detail page,
    // but NOT on /serverless/new (that one belongs to "All workers").
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
        <SidebarGroup label="The Hub" open={hubOpen} setOpen={setHubOpen}>
          {HUB.map((item) => (
            <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
          ))}
        </SidebarGroup>

        <SidebarGroup label="Resources" open={resOpen} setOpen={setResOpen}>
          {RESOURCES.map((item) => (
            <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
          ))}
        </SidebarGroup>

        <SidebarGroup label="Account" open={accountOpen} setOpen={setAccountOpen}>
          {ACCOUNT.map((item) => (
            <SidebarItem key={item.label} item={item} active={isActive(item.href)} />
          ))}
        </SidebarGroup>
      </nav>
    </aside>
  );
}

function SidebarGroup({
  label,
  open,
  setOpen,
  children,
}: {
  label: string;
  open: boolean;
  setOpen: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className="mt-2 flex w-full items-center gap-1 px-4 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <ChevronDown
          className={cn("h-3 w-3 transition-transform", open ? "rotate-0" : "-rotate-90")}
        />
        {label}
      </button>
      {open && <ul className="space-y-px px-2">{children}</ul>}
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
