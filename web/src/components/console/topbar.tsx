"use client";

import Link from "next/link";
import { ChevronRight, LogIn, Menu, PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";
import { useSidebarState } from "./sidebar-state";

export type Crumb = { label: string; href?: string };

export function ConsoleTopbar({
  crumbs = [],
  username,
}: {
  crumbs?: Crumb[];
  username?: string;
}) {
  const { togglePanel } = useSidebarState();
  // Trim breadcrumbs on phones so the header doesn't wrap.
  const lastCrumb = crumbs[crumbs.length - 1];
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border bg-sidebar px-3 lg:px-4">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={togglePanel}
          className="inline-flex shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Toggle sidebar"
        >
          <PanelLeft className="hidden h-4 w-4 md:block" />
          <Menu className="h-4 w-4 md:hidden" />
        </button>
        <nav className="ml-2 hidden min-w-0 items-center gap-1 text-sm md:flex">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1 truncate text-muted-foreground">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5" />}
              {c.href ? (
                <Link href={c.href} className="truncate hover:text-foreground">
                  {c.label}
                </Link>
              ) : (
                <span className="truncate text-foreground">{c.label}</span>
              )}
            </span>
          ))}
        </nav>
        {lastCrumb && (
          <span className="ml-1 truncate text-sm text-foreground md:hidden">
            {lastCrumb.label}
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <ThemeToggle />
        {username ? (
          <UserMenu username={username} />
        ) : (
          <Button asChild variant="outline" size="sm">
            <Link href="/login">
              <LogIn className="h-4 w-4" />
              Sign in
            </Link>
          </Button>
        )}
      </div>
    </header>
  );
}
