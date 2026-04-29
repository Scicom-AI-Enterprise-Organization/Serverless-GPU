"use client";

import Link from "next/link";
import { ChevronRight, LogIn, PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";

export type Crumb = { label: string; href?: string };

export function ConsoleTopbar({
  crumbs = [],
  username,
}: {
  crumbs?: Crumb[];
  username?: string;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-sidebar px-3 lg:px-4">
      <div className="flex items-center gap-2">
        <button
          className="hidden rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground md:inline-flex"
          aria-label="Toggle sidebar"
        >
          <PanelLeft className="h-4 w-4" />
        </button>
        <nav className="ml-2 hidden items-center gap-1 text-sm md:flex">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1 text-muted-foreground">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5" />}
              {c.href ? (
                <Link href={c.href} className="hover:text-foreground">
                  {c.label}
                </Link>
              ) : (
                <span className="text-foreground">{c.label}</span>
              )}
            </span>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-1">
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
