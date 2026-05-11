"use client";

import { useEffect, useState } from "react";

/** Format a USD cost.
 * - `< 0.01`  → 4 decimal places ($0.0034)
 * - `< 100`   → 2 decimal places ($1.23)
 * - otherwise → 2 decimal places ($1,234.56) with thousands separator
 */
export function formatCostUSD(cost: number | null | undefined): string {
  if (cost == null || !Number.isFinite(cost)) return "—";
  if (cost < 0.01) {
    return `$${cost.toFixed(4)}`;
  }
  return cost.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Format a rate as $X.XX/hr. */
export function formatRateUSD(rate: number | null | undefined): string {
  if (rate == null || !Number.isFinite(rate)) return "—";
  return `${formatCostUSD(rate)}/hr`;
}

/** Compute accumulated cost for a single resource.
 *
 * `startedAt` / `endedAt` are ISO timestamps. When `endedAt` is null the
 * resource is still running and the elapsed time grows toward `now`. */
export function computeCost(
  startedAt: string | null | undefined,
  endedAt: string | null | undefined,
  costPerHr: number | null | undefined,
  now: number = Date.now(),
): number | null {
  if (!startedAt || costPerHr == null || !Number.isFinite(costPerHr)) return null;
  const start = new Date(startedAt).getTime();
  if (!Number.isFinite(start)) return null;
  const end = endedAt ? new Date(endedAt).getTime() : now;
  const elapsedMs = Math.max(0, end - start);
  return (elapsedMs / 3_600_000) * costPerHr;
}

/** Hook: returns the live accumulated cost for a resource, refreshed every
 * `intervalMs` while it's still running. When `endedAt` is set the value is
 * fixed (no interval is armed). Returns null if we don't have enough info to
 * compute. */
export function useLiveCost(
  startedAt: string | null | undefined,
  endedAt: string | null | undefined,
  costPerHr: number | null | undefined,
  intervalMs: number = 1000,
): number | null {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (endedAt) return; // frozen
    if (!startedAt || costPerHr == null) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [startedAt, endedAt, costPerHr, intervalMs]);
  return computeCost(startedAt, endedAt, costPerHr, now);
}
