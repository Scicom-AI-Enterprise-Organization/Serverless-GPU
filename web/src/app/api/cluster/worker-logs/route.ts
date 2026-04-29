// Returns recent gateway log lines mentioning the requested machine_id.
// Used by the WorkersTab's expanded row to show provision / register /
// scale / terminate events for a specific RunPod worker, near-real-time
// via polling.
//
// Limitation: this is "gateway-side" logs only — the events the gateway
// itself emits when managing the worker. The worker container's own stdout
// (vLLM startup, inference traces) lives on the RunPod pod; RunPod's REST
// API doesn't expose pod logs, so streaming them needs the worker to ship
// log lines into Redis itself.
//
// Same dev-mode shape as /api/cluster/queue — uses `kubectl logs` from the
// UI server. Needs a valid kubectl credential.

import { exec } from "node:child_process";
import { promisify } from "node:util";
import { NextRequest, NextResponse } from "next/server";

const run = promisify(exec);

const NS = process.env.SERVERLESSGPU_NAMESPACE ?? "serverlessgpu";
const DEPLOY = process.env.SERVERLESSGPU_GATEWAY_DEPLOY ?? "serverlessgpu-gateway";
const MACHINE_RX = /^m-[a-z0-9-]{1,40}$/i;

function shellQuote(s: string) {
  return `'${s.replace(/'/g, `'"'"'`)}'`;
}

export async function GET(req: NextRequest) {
  const machineId = req.nextUrl.searchParams.get("machine_id") ?? "";
  if (!MACHINE_RX.test(machineId)) {
    return NextResponse.json({ error: "invalid or missing machine_id" }, { status: 400 });
  }
  const tail = Math.min(Number(req.nextUrl.searchParams.get("tail") ?? 500), 5000);

  // kubectl logs … then grep on the host for performance + to keep the
  // payload small. We pull more than we render (tail*2) so the grep yields
  // at least something even if 99% of the gateway log is for other machines.
  const args = [
    "-n", NS, "logs", `deploy/${DEPLOY}`,
    "--tail", String(tail * 2),
  ];
  const cmd = `kubectl ${args.map(shellQuote).join(" ")} | grep --color=never -F ${shellQuote(machineId)} || true`;

  try {
    const { stdout } = await run(cmd, { timeout: 12_000, maxBuffer: 4 * 1024 * 1024 });
    const lines = stdout
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .slice(-tail); // keep most recent N matches

    return NextResponse.json({
      machine_id: machineId,
      lines,
      truncated: lines.length === tail,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const isAuth = /tsh|teleport|login|unauthorized|relogin/i.test(msg);
    return NextResponse.json(
      {
        error: msg,
        hint: isAuth ? "kubectl creds expired — `tsh kube login <cluster>`" : undefined,
      },
      { status: isAuth ? 401 : 502 },
    );
  }
}
