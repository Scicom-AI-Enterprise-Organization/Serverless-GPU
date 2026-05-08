"use client";

import { useMemo } from "react";
import yaml from "js-yaml";
import { CheckCircle2, Clock, Layers, TrendingUp } from "lucide-react";
import type { BenchmarkRecord } from "@/lib/types";

/** Decorated row used by chart + stats. Pulls out info from result_json + config_yaml
 * once per benchmark so we don't re-parse on every render. */
type Decorated = {
  bench: BenchmarkRecord;
  output_throughput: number | null;
  median_ttft_ms: number | null;
  gpu_type: string | null;
  gpu_count: number | null;
  model: string | null;
  durationS: number | null;
};

function decorate(bench: BenchmarkRecord): Decorated {
  const r = (bench.result_json ?? {}) as Record<string, unknown>;
  let gpu_type: string | null = null;
  let gpu_count: number | null = null;
  let model: string | null = null;
  try {
    const cfg = yaml.load(bench.config_yaml) as
      | { runpod?: { pod?: { gpu_type?: string; gpu_count?: number } }; benchmark?: Array<{ model?: { repo_id?: string } }> }
      | null;
    gpu_type = cfg?.runpod?.pod?.gpu_type ?? null;
    gpu_count = cfg?.runpod?.pod?.gpu_count ?? null;
    model = cfg?.benchmark?.[0]?.model?.repo_id ?? null;
  } catch {
    // ignore — show "—" in the UI
  }
  const start = bench.started_at ? new Date(bench.started_at).getTime() : null;
  const end = bench.ended_at ? new Date(bench.ended_at).getTime() : null;
  return {
    bench,
    output_throughput:
      typeof r.output_throughput === "number" ? r.output_throughput : null,
    median_ttft_ms:
      typeof r.median_ttft_ms === "number" ? r.median_ttft_ms : null,
    gpu_type,
    gpu_count,
    model,
    durationS:
      start != null && end != null ? Math.max(0, Math.round((end - start) / 1000)) : null,
  };
}

function shortGpu(name: string | null): string {
  if (!name) return "—";
  // "NVIDIA GeForce RTX 4090" → "RTX 4090"
  return name
    .replace(/^NVIDIA\s+/i, "")
    .replace(/^GeForce\s+/i, "")
    .replace(/\s+80GB\s+(HBM3|PCIe).*$/i, " 80GB");
}

function shortModel(name: string | null): string {
  if (!name) return "—";
  return name.split("/").pop() ?? name;
}

export function BenchmarkDashboard({ items }: { items: BenchmarkRecord[] }) {
  const decorated = useMemo(() => items.map(decorate), [items]);

  const total = items.length;
  const done = items.filter((b) => b.status === "done").length;
  const failed = items.filter((b) => b.status === "failed").length;
  const running = items.filter((b) => b.status === "running" || b.status === "queued").length;
  const terminal = done + failed;
  const passRate = terminal > 0 ? (done / terminal) * 100 : null;

  const totalGpuMinutes = decorated.reduce((acc, d) => {
    if (d.durationS == null) return acc;
    const gpus = d.gpu_count ?? 1;
    return acc + (d.durationS * gpus) / 60;
  }, 0);

  const bestRun = decorated
    .filter((d) => d.output_throughput != null)
    .sort((a, b) => (b.output_throughput ?? 0) - (a.output_throughput ?? 0))[0];

  if (items.length === 0) return null;

  return (
    <section className="mb-8 space-y-5">
      {/* Stats row — all neutral. KPI cards aren't status; they're just numbers. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          icon={<Layers className="h-4 w-4" />}
          label="Total runs"
          value={total.toString()}
          sub={
            running > 0
              ? `${running} in flight`
              : `${done} done · ${failed} failed`
          }
        />
        <StatCard
          icon={<CheckCircle2 className="h-4 w-4" />}
          label="Pass rate"
          value={passRate != null ? `${passRate.toFixed(0)}%` : "—"}
          sub={terminal > 0 ? `${done}/${terminal} completed` : "no completed runs"}
        />
        <StatCard
          icon={<TrendingUp className="h-4 w-4" />}
          label="Best throughput"
          value={
            bestRun?.output_throughput
              ? `${bestRun.output_throughput.toFixed(0)} tok/s`
              : "—"
          }
          sub={
            bestRun
              ? `${shortModel(bestRun.model)} · ${shortGpu(bestRun.gpu_type)}`
              : "no runs with results yet"
          }
        />
        <StatCard
          icon={<Clock className="h-4 w-4" />}
          label="GPU minutes"
          value={totalGpuMinutes >= 60
            ? `${(totalGpuMinutes / 60).toFixed(1)} h`
            : `${totalGpuMinutes.toFixed(0)} m`}
          sub={`across ${total} run${total === 1 ? "" : "s"}`}
        />
      </div>

    </section>
  );
}

function StatCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-muted text-muted-foreground">
          {icon}
        </div>
        <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}
