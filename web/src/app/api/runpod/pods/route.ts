// Server-side proxy to RunPod's REST API. Filters pods by the gateway's
// naming convention (`<prefix>-<appId>-<machine_id>`) so the WorkersTab
// only sees pods belonging to the requested endpoint.
//
// RUNPOD_API_KEY stays server-side. Browser hits /api/runpod/pods?app=<id>.

import { NextRequest, NextResponse } from "next/server";

const RUNPOD_BASE =
  process.env.RUNPOD_API_BASE?.replace(/\/$/, "") ?? "https://rest.runpod.io/v1";
const NAME_PREFIX = process.env.RUNPOD_NAME_PREFIX ?? "serverlessgpu";

type RunpodMachine = {
  gpuTypeId?: string;
  gpuTypeIds?: string[];
  vcpuCount?: number;
  memoryInGb?: number;
  dataCenterId?: string;
};

type RunpodPod = {
  id: string;
  name: string;
  desiredStatus?: string;
  currentStatus?: string;
  dataCenterId?: string;
  machine?: RunpodMachine;
  gpuCount?: number;
  vcpuCount?: number;
  memoryInGb?: number;
  containerDiskInGb?: number;
  costPerHr?: number;
  createdAt?: string;
};

export type WorkerRowResponse = {
  machine_id: string;
  pod_id: string;
  status: "running" | "initializing" | "terminating" | "terminated" | "unknown";
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

const apiKey = () => process.env.RUNPOD_API_KEY ?? "";

export async function GET(req: NextRequest) {
  const key = apiKey();
  if (!key) {
    return NextResponse.json(
      { error: "RUNPOD_API_KEY is not set on the UI server" },
      { status: 503 },
    );
  }

  const appId = req.nextUrl.searchParams.get("app");
  if (!appId) {
    return NextResponse.json({ error: "missing ?app=<appId>" }, { status: 400 });
  }

  try {
    const r = await fetch(`${RUNPOD_BASE}/pods`, {
      headers: { Authorization: `Bearer ${key}` },
      cache: "no-store",
    });
    if (!r.ok) {
      const text = await r.text();
      return NextResponse.json(
        { error: `runpod ${r.status}: ${text || r.statusText}` },
        { status: 502 },
      );
    }
    const pods = (await r.json()) as RunpodPod[] | null;
    const namePrefix = `${NAME_PREFIX}-${appId}-`;

    const rows: WorkerRowResponse[] = (pods ?? [])
      .filter((p) => (p.name ?? "").startsWith(namePrefix))
      .map(toRow);

    return NextResponse.json({ workers: rows, prefix: namePrefix });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}

function toRow(pod: RunpodPod): WorkerRowResponse {
  const idx = pod.name.indexOf("m-rp-");
  const machineId = idx >= 0 ? pod.name.slice(idx) : pod.name;

  const region = pod.dataCenterId ?? pod.machine?.dataCenterId ?? "";
  const regionCode = region.split("-")[0] || region.slice(0, 2).toUpperCase();

  const gpuId =
    pod.machine?.gpuTypeId ??
    pod.machine?.gpuTypeIds?.[0] ??
    "—";

  return {
    machine_id: machineId,
    pod_id: pod.id,
    status: mapStatus(pod.desiredStatus, pod.currentStatus),
    raw_status: (pod.currentStatus || pod.desiredStatus || "").toLowerCase(),
    region,
    region_code: regionCode,
    gpu: prettyGpu(gpuId),
    gpu_count: pod.gpuCount ?? 1,
    vcpus: pod.vcpuCount ?? pod.machine?.vcpuCount ?? 0,
    ram_gb: pod.memoryInGb ?? pod.machine?.memoryInGb ?? 0,
    disk_gb: pod.containerDiskInGb ?? 0,
    created_at: pod.createdAt ?? null,
  };
}

function mapStatus(
  desired?: string,
  current?: string,
): WorkerRowResponse["status"] {
  const d = (desired || "").toUpperCase();
  const c = (current || "").toUpperCase();
  if (c === "RUNNING" || d === "RUNNING") return "running";
  if (c === "EXITED" || c === "TERMINATED" || d === "TERMINATED" || d === "EXITED")
    return "terminated";
  if (d === "STOPPED" || c === "STOPPING") return "terminating";
  if (c === "STARTING" || c === "CREATED" || d === "CREATED") return "initializing";
  return "unknown";
}

function prettyGpu(id: string): string {
  // "NVIDIA H100 80GB HBM3" → "H100 80GB"; lowercase passthroughs keep their form.
  if (!id || id === "—") return "—";
  const stripped = id.replace(/^NVIDIA\s+/i, "").replace(/^GeForce\s+/i, "");
  return stripped;
}
