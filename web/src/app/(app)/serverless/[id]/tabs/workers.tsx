"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { AppRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

type WorkerStatus = "running" | "initializing" | "terminating" | "terminated" | "unknown";

type WorkerRow = {
  machine_id: string;
  pod_id: string;
  status: WorkerStatus;
  raw_status: string;
  region: string;
  region_code: string;
  gpu: string;
  gpu_count: number;
  vcpus: number;
  ram_gb: number;
  disk_gb: number;
  created_at: string | null;
};

type ApiResponse = { workers: WorkerRow[]; prefix: string; error?: string };

const STATUS_STYLES: Record<WorkerStatus, string> = {
  running:      "bg-status-active/15 text-status-active",
  initializing: "bg-status-idle/15 text-status-idle",
  terminating:  "bg-status-down/15 text-status-down",
  terminated:   "bg-muted text-muted-foreground",
  unknown:      "bg-muted text-muted-foreground",
};

const POLL_MS = 10_000;
const STORAGE_KEY = (appId: string) => `serverless-ui:workers:${appId}`;

export function WorkersTab({ app }: { app: AppRecord }) {
  const [live, setLive] = useState<WorkerRow[] | null>(null);
  const [remembered, setRemembered] = useState<WorkerRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY(app.app_id));
      if (raw) setRemembered(JSON.parse(raw));
    } catch {
      // ignore
    }
  }, [app.app_id]);

  const fetchLive = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(`/api/runpod/pods?app=${encodeURIComponent(app.app_id)}`, {
        cache: "no-store",
      });
      const body = (await r.json()) as ApiResponse;
      if (!r.ok) throw new Error(body?.error ?? r.statusText);
      setLive(body.workers);

      setRemembered((prev) => {
        const map = new Map(prev.map((w) => [w.machine_id, w]));
        for (const w of body.workers) map.set(w.machine_id, w);
        const merged = Array.from(map.values());
        try {
          window.localStorage.setItem(STORAGE_KEY(app.app_id), JSON.stringify(merged));
        } catch {
          // best-effort persist
        }
        return merged;
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [app.app_id]);

  useEffect(() => {
    fetchLive();
    const id = window.setInterval(fetchLive, POLL_MS);
    return () => window.clearInterval(id);
  }, [fetchLive]);

  const rows = useMemo(() => {
    // Until the first fetch lands, we don't actually know which cached
    // workers are still alive — show them with their last-known status
    // instead of flashing "terminated" before the real data arrives.
    if (live === null) {
      const order: WorkerStatus[] = ["running", "initializing", "terminating", "unknown", "terminated"];
      return [...remembered].sort((a, b) => {
        const oa = order.indexOf(a.status);
        const ob = order.indexOf(b.status);
        if (oa !== ob) return oa - ob;
        return (b.created_at ?? "").localeCompare(a.created_at ?? "");
      });
    }
    const liveIds = new Set(live.map((w) => w.machine_id));
    const ghosts: WorkerRow[] = remembered
      .filter((w) => !liveIds.has(w.machine_id))
      .map((w) => ({ ...w, status: "terminated" as const, raw_status: "terminated" }));
    const order: WorkerStatus[] = ["running", "initializing", "terminating", "unknown", "terminated"];
    const all = [...live, ...ghosts];
    return all.sort((a, b) => {
      const oa = order.indexOf(a.status);
      const ob = order.indexOf(b.status);
      if (oa !== ob) return oa - ob;
      return (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });
  }, [live, remembered]);

  function clearHistory() {
    try {
      window.localStorage.removeItem(STORAGE_KEY(app.app_id));
    } catch {
      // ignore
    }
    setRemembered([]);
  }

  const liveCount = live === null ? "—" : live.length;
  const terminatedCount = rows.filter((r) => r.status === "terminated").length;

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-2 text-xs">
        <div className="flex items-center gap-3">
          <span className="text-muted-foreground">
            <span className="font-mono text-foreground">{liveCount}</span> live
          </span>
          <span className="text-muted-foreground">
            <span className="font-mono text-foreground">{terminatedCount}</span> remembered terminated
          </span>
          <span className="text-muted-foreground">max {app.autoscaler.max_containers}</span>
        </div>
        <div className="flex items-center gap-2">
          {terminatedCount > 0 && (
            <Button variant="ghost" size="xs" onClick={clearHistory}>
              Clear history
            </Button>
          )}
          <Button variant="outline" size="xs" onClick={fetchLive} disabled={loading}>
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            Refresh
          </Button>
        </div>
      </div>
      {err && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {err}
        </div>
      )}
      <CardContent className="px-0 py-0">
        <table className="w-full text-sm">
          <thead className="border-b border-border bg-muted/20 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="w-6 px-2 py-2"></th>
              <th className="px-4 py-2 font-medium">Worker ID</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Region</th>
              <th className="px-4 py-2 font-medium">GPU</th>
              <th className="px-4 py-2 font-medium">vCPUs</th>
              <th className="px-4 py-2 font-medium">RAM</th>
              <th className="px-4 py-2 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => (
              <WorkerRow key={w.machine_id} w={w} />
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-12 text-center text-sm text-muted-foreground">
                  {loading ? "Loading workers from RunPod…" : "No workers — fire a request to trigger the autoscaler."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function WorkerRow({ w }: { w: WorkerRow }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className={cn("border-b border-border/60 last:border-b-0", w.status === "terminated" && "opacity-60")}>
        <td className="px-2 py-3 align-middle">
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center justify-center text-muted-foreground hover:text-foreground"
            aria-label={open ? "Hide logs" : "Show logs"}
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        </td>
        <td className="px-4 py-3 font-mono text-xs">{w.machine_id}</td>
        <td className="px-4 py-3">
          <span className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs",
            STATUS_STYLES[w.status],
          )}>
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {w.status}
          </span>
        </td>
        <td className="px-4 py-3">
          {w.region ? (
            <span className="inline-flex items-center gap-2">
              <span className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[10px]">
                {w.region_code}
              </span>
              <span className="font-mono text-xs">{w.region}</span>
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </td>
        <td className="px-4 py-3 font-mono text-xs">{w.gpu}{w.gpu_count > 1 ? ` × ${w.gpu_count}` : ""}</td>
        <td className="px-4 py-3 font-mono text-xs">{w.vcpus || "—"}</td>
        <td className="px-4 py-3 font-mono text-xs">{w.ram_gb ? `${w.ram_gb} GB` : "—"}</td>
        <td className="px-4 py-3 text-xs text-muted-foreground">
          {w.created_at ? new Date(w.created_at).toLocaleString() : "—"}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border/60 bg-muted/20">
          <td colSpan={8} className="px-4 py-3">
            <WorkerLogs machineId={w.machine_id} />
          </td>
        </tr>
      )}
    </>
  );
}

function WorkerLogs({ machineId }: { machineId: string }) {
  const [lines, setLines] = useState<string[]>([]);
  const [err, setErr] = useState<{ msg: string; hint?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoTail, setAutoTail] = useState(true);

  const fetchLogs = useCallback(async () => {
    try {
      const r = await fetch(
        `/api/cluster/worker-logs?machine_id=${encodeURIComponent(machineId)}&tail=300`,
        { cache: "no-store" },
      );
      const body = (await r.json()) as { lines?: string[]; error?: string; hint?: string };
      if (!r.ok) {
        setErr({ msg: body.error ?? r.statusText, hint: body.hint });
        return;
      }
      setErr(null);
      setLines(body.lines ?? []);
    } catch (e) {
      setErr({ msg: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  }, [machineId]);

  useEffect(() => {
    fetchLogs();
    if (!autoTail) return;
    const id = window.setInterval(fetchLogs, 2500);
    return () => window.clearInterval(id);
  }, [fetchLogs, autoTail]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <div className="flex items-center gap-3">
          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-primary">
            gateway events
          </span>
          <span className="font-mono">machine = {machineId}</span>
          {loading && <Loader2 className="h-3 w-3 animate-spin" />}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[10px]">
            <input
              type="checkbox"
              checked={autoTail}
              onChange={(e) => setAutoTail(e.target.checked)}
              className="h-3 w-3"
            />
            tail (poll every 2.5s)
          </label>
          <Button variant="outline" size="xs" onClick={fetchLogs}>
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>
      </div>

      {err ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <div className="font-medium">{err.msg}</div>
          {err.hint && <div className="mt-1 opacity-80">{err.hint}</div>}
        </div>
      ) : lines.length === 0 ? (
        <div className="rounded-md border border-dashed border-border bg-background/40 px-3 py-4 text-center text-xs text-muted-foreground">
          {loading ? "loading…" : "no gateway events for this worker yet"}
        </div>
      ) : (
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-background/60 p-3 font-mono text-[11px] leading-relaxed scrollbar-thin">
          {lines.map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </pre>
      )}

      <p className="text-[10px] leading-relaxed text-muted-foreground">
        Source: <code className="font-mono">kubectl logs deploy/serverlessgpu-gateway | grep {machineId}</code>.
        These are <strong>gateway-side</strong> events (provision, register, scale,
        terminate). Container stdout from the worker pod itself isn&apos;t available — RunPod&apos;s
        public API has no logs endpoint.
      </p>
    </div>
  );
}
