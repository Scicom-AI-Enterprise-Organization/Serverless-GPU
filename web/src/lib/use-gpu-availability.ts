"use client";

import { useEffect, useRef, useState } from "react";
import type { GpuAvailability } from "./gateway";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; data: GpuAvailability }
  | { status: "error"; message: string };

export function useGpuAvailability(
  gpu: string,
  count: number,
  enabled = true,
  cloudType?: "COMMUNITY" | "SECURE",
  /**
   * Optional provider routing. When kind === "pi" we hit the PI-specific
   * availability endpoint with the given provider_id (so the right API key
   * is used). Defaults to the RunPod path for backwards compat.
   */
  provider?: { kind: "runpod" | "pi"; id: string | null },
): State {
  const [state, setState] = useState<State>({ status: "idle" });
  const reqId = useRef(0);
  const providerKind = provider?.kind ?? "runpod";
  const providerId = provider?.id ?? null;

  useEffect(() => {
    if (!enabled || !gpu || count < 1) {
      setState({ status: "idle" });
      return;
    }
    const id = ++reqId.current;
    setState({ status: "loading" });
    const t = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ gpu, count: String(count) });
        if (cloudType) params.set("cloud_type", cloudType);
        let url: string;
        if (providerKind === "pi") {
          if (providerId) params.set("provider_id", providerId);
          url = `/api/proxy/compute/pi/availability?${params.toString()}`;
        } else {
          url = `/api/proxy/v1/availability?${params.toString()}`;
        }
        const res = await fetch(url, { cache: "no-store" });
        if (id !== reqId.current) return;
        if (!res.ok) {
          const body = await res.text().catch(() => "");
          setState({
            status: "error",
            message: `gateway ${res.status}: ${body || res.statusText}`,
          });
          return;
        }
        const data = (await res.json()) as GpuAvailability;
        if (id !== reqId.current) return;
        setState({ status: "ok", data });
      } catch (e) {
        if (id !== reqId.current) return;
        setState({
          status: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    }, 300);
    return () => clearTimeout(t);
  }, [gpu, count, enabled, cloudType, providerKind, providerId]);

  return state;
}
