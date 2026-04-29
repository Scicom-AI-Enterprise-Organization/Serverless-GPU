// Dev-only proxy that reads the per-app job queue straight out of the
// gateway's Redis using `kubectl exec`. This bypasses the missing
// `GET /apps/{id}/queue` gateway endpoint so the Queue tab has something to
// render today. When the gateway gains a real endpoint, swap the implementation.
//
// Requires:
//   - kubectl on PATH and authenticated against the cluster
//     (e.g. `tsh kube login` for Teleport)
//   - serverlessgpu/serverlessgpu-redis-0 pod
//   - serverlessgpu/serverlessgpu-redis-auth secret with REDIS_PASSWORD
//
// Note: this would be unsafe in production — kubectl creds in the UI's
// runtime is a power amplification. Acceptable for `npm run dev`.

import { exec } from "node:child_process";
import { promisify } from "node:util";
import { NextRequest, NextResponse } from "next/server";

const run = promisify(exec);

const NS = process.env.SERVERLESSGPU_NAMESPACE ?? "serverlessgpu";
const REDIS_POD = process.env.SERVERLESSGPU_REDIS_POD ?? "serverlessgpu-redis-0";
const REDIS_SECRET = process.env.SERVERLESSGPU_REDIS_SECRET ?? "serverlessgpu-redis-auth";
const APP_ID_RX = /^[a-z0-9][a-z0-9._-]{0,127}$/i;

let cachedPw: string | null = null;

async function getRedisPw(): Promise<string> {
  if (cachedPw) return cachedPw;
  const { stdout } = await run(
    `kubectl -n ${NS} get secret ${REDIS_SECRET} -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d`,
    { timeout: 10_000 },
  );
  const pw = stdout.trim();
  if (!pw) throw new Error("REDIS_PASSWORD secret is empty");
  cachedPw = pw;
  return pw;
}

async function redis(cmd: string[]): Promise<string> {
  const pw = await getRedisPw();
  // `kubectl exec -- redis-cli -a <pw> ...` — pw goes via argv inside the pod
  // (safer than embedding in a shell string). Each cmd token is passed
  // through verbatim; we still validate the user-supplied app_id upstream.
  const args = ["-n", NS, "exec", REDIS_POD, "--", "redis-cli", "-a", pw, "--no-auth-warning", ...cmd];
  return execKubectl(args);
}

async function redisScan(pattern: string): Promise<string> {
  const pw = await getRedisPw();
  const args = ["-n", NS, "exec", REDIS_POD, "--", "redis-cli", "-a", pw, "--no-auth-warning", "--scan", "--pattern", pattern];
  return execKubectl(args);
}

function execKubectl(args: string[]): Promise<string> {
  // Use spawn-style escaping by passing argv through `bash -c "kubectl ..."`
  // via shell-quoting each arg.
  const quoted = args.map(shellQuote).join(" ");
  return run(`kubectl ${quoted}`, { timeout: 15_000, maxBuffer: 4 * 1024 * 1024 }).then(
    ({ stdout }) => stdout,
  );
}

function shellQuote(s: string) {
  return `'${s.replace(/'/g, `'"'"'`)}'`;
}

type QueueJob = {
  request_id: string;
  payload?: unknown;
  timeout_s?: number;
  endpoint?: string;
  stream?: boolean;
};

type ResultBlob = {
  status?: string;
  output?: {
    model?: string;
    [k: string]: unknown;
  } | null;
};

type Bucket = "in queue" | "in progress" | "completed" | "failed";

type Item = QueueJob & {
  bucket: Bucket;
  status: string;
  output?: unknown;
  has_result: boolean;
};

const RECENT_RESULT_LIMIT = 80;

// In-memory payload cache. The gateway loses the original input the moment a
// worker pops the job (it only stores the result blob), so we snapshot every
// queue item we ever observe here. Module-scoped so it survives across
// requests in the same Next.js process. Cleared on dev-server restart.
//
// TTL matches the gateway's result-key TTL (1h) to avoid unbounded growth.
type CachedJob = { payload?: unknown; endpoint?: string; stream?: boolean; timeout_s?: number; ts: number };
const PAYLOAD_CACHE: Map<string, CachedJob> = new Map();
const PAYLOAD_TTL_MS = 60 * 60 * 1000;

function rememberPayload(j: QueueJob) {
  if (!j.request_id) return;
  PAYLOAD_CACHE.set(j.request_id, {
    payload: j.payload,
    endpoint: j.endpoint,
    stream: j.stream,
    timeout_s: j.timeout_s,
    ts: Date.now(),
  });
}

function gcPayloads() {
  const now = Date.now();
  for (const [k, v] of PAYLOAD_CACHE) {
    if (now - v.ts > PAYLOAD_TTL_MS) PAYLOAD_CACHE.delete(k);
  }
}

export async function GET(req: NextRequest) {
  const appId = req.nextUrl.searchParams.get("app") ?? "";
  if (!APP_ID_RX.test(appId)) {
    return NextResponse.json({ error: "invalid or missing app" }, { status: 400 });
  }

  try {
    // Pull queue + worker count + all live result keys in parallel. Result
    // keys carry a 1h TTL so SCAN size stays bounded; we cap downstream too.
    const [queueRaw, workerCountRaw, resultKeysRaw] = await Promise.all([
      redis(["LRANGE", `queue:${appId}`, "0", "199"]),
      redis(["SCARD", `worker_index:${appId}`]),
      redisScan("result:req-*"),
    ]);

    const queueJobs: QueueJob[] = queueRaw
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        try {
          return JSON.parse(l) as QueueJob;
        } catch {
          return { request_id: l };
        }
      });

    // Snapshot every queue item we see so its payload survives after the
    // worker pops it.
    queueJobs.forEach(rememberPayload);
    gcPayloads();

    // Build a map of request_id → blob for everything queued so we can
    // mark each row's true status (the worker may have started it already).
    const queuedIds = new Set(queueJobs.map((j) => j.request_id));

    const allKeys = resultKeysRaw
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.startsWith("result:req-"));

    // Fetch only as many result blobs as we need (pending ones can't be
    // attributed to an app from the blob alone, so we have to inspect them).
    // Cap to RECENT_RESULT_LIMIT to keep the round-trip cheap.
    const targetKeys = [
      ...new Set([
        ...queueJobs.map((j) => `result:${j.request_id}`),
        ...allKeys.slice(0, RECENT_RESULT_LIMIT),
      ]),
    ];

    const blobs: Record<string, ResultBlob> = {};
    if (targetKeys.length > 0) {
      // MGET avoids N round-trips. Returns "(nil)\n" lines for missing keys.
      const mgetOut = await redis(["MGET", ...targetKeys]);
      const lines = mgetOut.split("\n");
      targetKeys.forEach((k, i) => {
        const raw = lines[i] ?? "";
        if (!raw || raw === "(nil)") return;
        try {
          blobs[k] = JSON.parse(raw);
        } catch {
          // ignore unparseable blob
        }
      });
    }

    // Assemble rows.
    const queuedItems: Item[] = queueJobs.map((j) => {
      const blob = blobs[`result:${j.request_id}`];
      const s = (blob?.status ?? "pending").toLowerCase();
      const bucket: Bucket =
        s === "completed" || s === "ready"
          ? "completed"
          : s === "in progress"
            ? "in progress"
            : "in queue";
      return {
        ...j,
        bucket,
        status: s,
        output: blob?.output ?? undefined,
        has_result: !!blob,
      };
    });

    // For results not currently in the queue: keep only those whose output
    // model field matches our app_id (vLLM responses set this), so we don't
    // leak other apps' completions into this view.
    const recentItems: Item[] = [];
    for (const k of allKeys) {
      if (recentItems.length >= RECENT_RESULT_LIMIT) break;
      const reqId = k.replace(/^result:/, "");
      if (queuedIds.has(reqId)) continue;
      const blob = blobs[k];
      if (!blob) continue;
      const model = blob.output && typeof blob.output === "object" ? blob.output.model : undefined;
      const status = (blob.status ?? "").toLowerCase();
      // Pending results have no output yet, so we can't attribute them to
      // an app from the blob alone — skip them. They'll appear once they
      // either land in the queue (already covered) or complete.
      if (model !== appId) continue;
      const cached = PAYLOAD_CACHE.get(reqId);
      recentItems.push({
        request_id: reqId,
        bucket: status === "completed" || status === "ready" ? "completed" : "in progress",
        status,
        output: blob.output ?? undefined,
        has_result: true,
        payload: cached?.payload,
        endpoint: cached?.endpoint,
        stream: cached?.stream,
        timeout_s: cached?.timeout_s,
      });
    }

    const items: Item[] = [...queuedItems, ...recentItems];

    return NextResponse.json({
      app_id: appId,
      queue_length: queueJobs.length,
      in_progress: items.filter((i) => i.bucket === "in progress").length,
      completed: items.filter((i) => i.bucket === "completed").length,
      worker_count: Number((workerCountRaw || "0").trim()) || 0,
      items,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    // The most common failure here is "tsh kube login" expired — surface
    // that clearly so the UI can prompt the user.
    const isAuth = /tsh|teleport|login|unauthorized|relogin/i.test(msg);
    return NextResponse.json(
      {
        error: msg,
        hint: isAuth
          ? "Run `tsh kube login <cluster>` and retry — kubectl creds expired."
          : undefined,
      },
      { status: isAuth ? 401 : 502 },
    );
  }
}
