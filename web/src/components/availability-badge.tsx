"use client";

import { AlertCircle, Check, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GpuAvailability } from "@/lib/gateway";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; data: GpuAvailability }
  | { status: "error"; message: string };

export function AvailabilityBadge({
  state,
  count,
  className,
}: {
  state: State;
  count: number;
  className?: string;
}) {
  if (state.status === "idle") return null;

  if (state.status === "loading") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-xs text-muted-foreground",
          className,
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        Checking availability…
      </span>
    );
  }

  if (state.status === "error") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400",
          className,
        )}
        title={state.message}
      >
        <AlertCircle className="h-3 w-3" />
        Couldn't check — try anyway
      </span>
    );
  }

  const { data } = state;

  if (data.available === null) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400",
          className,
        )}
        title={data.reason ?? undefined}
      >
        <AlertCircle className="h-3 w-3" />
        {data.reason ?? "Couldn't check — try anyway"}
      </span>
    );
  }

  if (data.available === false) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-xs text-red-600 dark:text-red-400",
          className,
        )}
        title={data.reason ?? undefined}
      >
        <X className="h-3 w-3" />
        {data.reason ?? "Not available"}
      </span>
    );
  }

  const totalPrice =
    data.cheapest_price_hr != null
      ? `$${(data.cheapest_price_hr * count).toFixed(2)}/hr`
      : null;
  const perGpu =
    count > 1 && data.cheapest_price_hr != null
      ? ` ($${data.cheapest_price_hr.toFixed(2)}/GPU)`
      : "";
  const regions =
    data.regions.length > 0
      ? ` · ${data.regions.length} region${data.regions.length === 1 ? "" : "s"}`
      : "";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400",
        className,
      )}
      title={data.regions.slice(0, 5).join(", ") || undefined}
    >
      <Check className="h-3 w-3" />
      Available{totalPrice ? ` · from ${totalPrice}${perGpu}` : ""}
      {regions}
    </span>
  );
}
