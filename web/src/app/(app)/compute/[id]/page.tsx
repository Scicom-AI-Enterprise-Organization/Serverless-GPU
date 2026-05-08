import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { ConsoleTopbar } from "@/components/console/topbar";
import { gateway, GatewayError } from "@/lib/gateway";
import type { ComputePod } from "@/lib/types";
import { currentUsername } from "@/lib/current-user";
import { PodDetail } from "./pod-detail";

async function loadPod(id: string): Promise<{ pod: ComputePod | null; error: string | null }> {
  try {
    const pod = await gateway.getCompute(id);
    return { pod, error: null };
  } catch (e) {
    if (e instanceof GatewayError && e.status === 404) return { pod: null, error: null };
    return { pod: null, error: e instanceof Error ? e.message : String(e) };
  }
}

export default async function ComputePodPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [{ pod, error }, username] = await Promise.all([
    loadPod(id),
    currentUsername(),
  ]);

  if (!pod && !error) notFound();

  return (
    <div className="flex h-full flex-col">
      <ConsoleTopbar
        crumbs={[{ label: "Compute", href: "/compute" }, { label: pod?.name ?? id }]}
        username={username}
      />
      <div className="flex-1 overflow-y-auto px-6 py-6 lg:px-10 lg:py-8 scrollbar-thin">
        <Link
          href="/compute"
          className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          All pods
        </Link>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        {pod && <PodDetail initial={pod} />}
      </div>
    </div>
  );
}
