"use client";

import { useEffect, useState } from "react";
import { Check, Flame, Lock } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  PAYWALLED_THEMES,
  TERMINAL_THEMES,
  type TerminalTheme,
  readTerminalTheme,
  writeTerminalTheme,
} from "@/lib/terminal-theme";

const META: Record<TerminalTheme, { label: string; description: string }> = {
  default: {
    label: "Default",
    description: "Neutral grey block, matches the rest of the UI.",
  },
  classic: {
    label: "Classic",
    description: "Black background, green phosphor text. Hacker vibes.",
  },
  rainbow: {
    label: "Rainbow",
    description: "Animated gradient sweep. Loud on purpose.",
  },
  sparkle: {
    label: "Sparkle",
    description: "Purple haze with twinkling stars — hover to summon more.",
  },
  christmas: {
    label: "Christmas",
    description: "Snowy night with falling flakes and a little snowman. Ho ho ho.",
  },
};

export function AppearanceSettings() {
  // Read from localStorage on mount (after hydration) so we don't render a
  // mismatched value during SSR.
  const [theme, setTheme] = useState<TerminalTheme>("rainbow");
  useEffect(() => {
    setTheme(readTerminalTheme());
  }, []);

  function pick(next: TerminalTheme) {
    if (PAYWALLED_THEMES.has(next)) {
      // UI-only paywall. No billing logic — just a vibe gate so people
      // see the "premium feel" before the actual cost-recovery feature lands.
      toast.error("Burn $100 first to unlock other terminal themes.");
      return;
    }
    setTheme(next);
    writeTerminalTheme(next);
  }

  return (
    <section className="rounded-lg border border-border bg-card p-5">
      <div className="mb-4">
        <h2 className="text-sm font-semibold">Terminal appearance</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Affects how SSH commands and other terminal-like blocks look on the
          Compute pages. Rainbow is free; the rest unlock once you&apos;ve burned
          enough on real workloads.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {TERMINAL_THEMES.map((t) => {
          const locked = PAYWALLED_THEMES.has(t);
          return (
            <button
              key={t}
              type="button"
              onClick={() => pick(t)}
              aria-disabled={locked}
              className={cn(
                "relative overflow-hidden rounded-lg border p-3 text-left transition-colors",
                theme === t
                  ? "border-foreground/60 ring-1 ring-foreground/20"
                  : "border-border",
                locked
                  ? "cursor-not-allowed hover:border-border"
                  : "hover:border-foreground/40",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span
                  className={cn(
                    "text-sm font-medium",
                    locked && "text-muted-foreground",
                  )}
                >
                  {META[t].label}
                </span>
                <div className="flex items-center gap-1">
                  {locked && (
                    <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400">
                      <Flame className="h-3 w-3" />
                      $100 to unlock
                    </span>
                  )}
                  {theme === t && !locked && (
                    <Check className="h-4 w-4 text-foreground" />
                  )}
                </div>
              </div>
              <p
                className={cn(
                  "mt-0.5 text-xs",
                  locked ? "text-muted-foreground/70" : "text-muted-foreground",
                )}
              >
                {META[t].description}
              </p>
              <div className={cn(locked && "opacity-40 grayscale")}>
                <Preview theme={t} />
              </div>
              {locked && (
                <div className="pointer-events-none absolute inset-0 flex items-end justify-end p-2">
                  <Lock className="h-4 w-4 text-muted-foreground/80" />
                </div>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}

// Preview swatch — uses dedicated `.terminal-preview-*` classes (defined in
// globals.css) so each option always renders its own theme regardless of
// what's currently active globally on <html>.
function Preview({ theme }: { theme: TerminalTheme }) {
  return (
    <div className="mt-3">
      <code className={cn("terminal-preview", `terminal-preview-${theme}`)}>
        ssh -i ~/.ssh/sgpu-runpod -p 39342 root@1.2.3.4
      </code>
    </div>
  );
}
