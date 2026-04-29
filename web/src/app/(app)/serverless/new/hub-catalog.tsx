"use client";

import { useMemo, useState } from "react";
import { Code2, Layers, Search, Sparkles, Star, Zap } from "lucide-react";
import { HUB_WORKERS } from "@/lib/hub-catalog";
import type { HubWorker } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DeployModal } from "./deploy-modal";

const CATEGORIES = ["All", "Image", "Video", "Audio", "Language", "Embedding"] as const;

export function HubCatalog() {
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("All");
  const [query, setQuery] = useState("");
  const [deployFor, setDeployFor] = useState<HubWorker | null>(null);

  const filtered = useMemo(
    () =>
      HUB_WORKERS.filter((w) => {
        if (category !== "All" && w.category !== category) return false;
        if (query && !`${w.name} ${w.description}`.toLowerCase().includes(query.toLowerCase())) {
          return false;
        }
        return true;
      }),
    [category, query],
  );

  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-6 flex items-end justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Create a new deployment</h1>
        <a
          className="text-sm text-primary hover:underline"
          href="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
          target="_blank"
          rel="noreferrer"
        >
          Not sure which to choose? Ask the assistant →
        </a>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <DeployTypeCard
          icon={<Layers className="h-5 w-5" />}
          title="Start from a Hub listing"
          subtitle="Choose from pre-built models and applications. No code or configuration required."
          active
        />
        <DeployTypeCard
          icon={<Zap className="h-5 w-5" />}
          title="Run code locally"
          subtitle="Write Python locally in your IDE, execute remotely on GPUs. Just add @endpoint."
          badge="Flash"
          comingSoon
        />
        <DeployTypeCard
          icon={<Code2 className="h-5 w-5" />}
          title="Custom deployment"
          subtitle="Configure a deployment from scratch with your own Docker image, GitHub repo, or custom template."
          comingSoon
        />
      </div>

      <h2 className="mt-10 text-lg font-medium">The Hub</h2>

      <div className="mt-3 flex items-center gap-2">
        <div className="flex flex-1 items-center gap-2 rounded-md border border-input bg-card px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the Hub"
            className="flex-1 bg-transparent text-sm placeholder:text-muted-foreground focus:outline-none"
          />
        </div>
        <button className="flex items-center gap-1 rounded-md border border-input bg-card px-3 py-2 text-sm text-muted-foreground hover:text-foreground">
          Most popular
        </button>
      </div>

      <div className="mt-3 flex gap-1 border-b border-border">
        {CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => setCategory(c)}
            className={cn(
              "relative px-3 py-2 text-sm transition-colors",
              category === c
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {c}
            {category === c && (
              <span className="absolute -bottom-px left-0 right-0 h-0.5 bg-primary" />
            )}
          </button>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {filtered.map((w) => (
          <WorkerCard key={w.slug} worker={w} onDeploy={() => setDeployFor(w)} />
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full rounded-md border border-dashed border-border bg-card/30 px-4 py-8 text-center text-sm text-muted-foreground">
            No workers match your filters.
          </div>
        )}
      </div>

      <DeployModal worker={deployFor} onClose={() => setDeployFor(null)} />
    </div>
  );
}

function DeployTypeCard({
  icon,
  title,
  subtitle,
  badge,
  active,
  comingSoon,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  badge?: string;
  active?: boolean;
  comingSoon?: boolean;
}) {
  return (
    <button
      disabled={comingSoon}
      className={cn(
        "relative overflow-hidden rounded-xl border bg-card px-5 py-4 text-left transition-colors",
        active
          ? "border-primary/60 ring-1 ring-primary/40"
          : "border-border hover:border-border/80 hover:bg-card/80",
        comingSoon && "cursor-not-allowed",
      )}
    >
      <div className={cn("flex items-start justify-between", comingSoon && "opacity-40")}>
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/15 text-primary">
          {icon}
        </div>
        {badge && (
          <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300">
            {badge}
          </span>
        )}
      </div>
      <div className={cn("mt-3 font-medium", comingSoon && "opacity-40")}>{title}</div>
      <p className={cn("mt-1 text-sm text-muted-foreground", comingSoon && "opacity-40")}>
        {subtitle}
      </p>
      {comingSoon && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/60 backdrop-blur-[2px]">
          <span className="rounded-full border border-border bg-card px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground shadow-sm">
            Coming soon
          </span>
        </div>
      )}
    </button>
  );
}

function WorkerCard({ worker, onDeploy }: { worker: HubWorker; onDeploy: () => void }) {
  return (
    <button onClick={onDeploy} className="text-left">
      <div className="group flex h-full cursor-pointer flex-col rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-card/80">
        <div className="flex items-start justify-between">
          <div
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-lg font-semibold",
              worker.iconBg,
            )}
          >
            {worker.iconLetter}
          </div>
          <span className="font-mono text-xs text-muted-foreground">{worker.version}</span>
        </div>
        <div className="mt-3 font-medium">{worker.name}</div>
        <p className="mt-1 line-clamp-3 flex-1 text-sm text-muted-foreground">
          {worker.description}
        </p>
        <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3 text-xs">
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <Sparkles className="h-3 w-3" />
            {worker.publisher}
          </span>
          <span className="flex items-center gap-1 text-muted-foreground">
            <Star className="h-3 w-3" />
            {worker.stars.toLocaleString()}
          </span>
        </div>
      </div>
    </button>
  );
}
