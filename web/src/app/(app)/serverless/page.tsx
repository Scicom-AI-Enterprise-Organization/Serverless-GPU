import Link from "next/link";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConsoleTopbar } from "@/components/console/topbar";
import { gateway } from "@/lib/gateway";
import type { AppRecord } from "@/lib/types";
import { currentUsername } from "@/lib/current-user";
import { EndpointGrid } from "./endpoint-grid";

async function loadEndpoints(): Promise<{ apps: AppRecord[]; error: string | null }> {
  try {
    const apps = await gateway.listApps();
    return { apps, error: null };
  } catch (e) {
    return { apps: [], error: e instanceof Error ? e.message : String(e) };
  }
}

export default async function ServerlessPage() {
  const [{ apps, error }, username] = await Promise.all([
    loadEndpoints(),
    currentUsername(),
  ]);

  return (
    <div className="flex h-full flex-col">
      <ConsoleTopbar crumbs={[{ label: "Serverless" }]} username={username} />
      <div className="flex-1 overflow-y-auto px-6 py-6 lg:px-10 lg:py-8 scrollbar-thin">
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Serverless</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Deploy and scale GPU-powered inference endpoints. Pay per second of compute.
            </p>
          </div>
          <Button asChild>
            <Link href="/serverless/new">
              <Plus className="h-4 w-4" />
              New endpoint
            </Link>
          </Button>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            Couldn&apos;t reach the gateway: {error}
          </div>
        )}

        <section>
          <div className="mb-3 flex items-baseline justify-between border-b border-border pb-2">
            <h2 className="text-base font-medium">Endpoints</h2>
            <span className="text-xs text-muted-foreground">
              {apps.length} {apps.length === 1 ? "endpoint" : "endpoints"}
            </span>
          </div>
          <EndpointGrid apps={apps} />
        </section>
      </div>
    </div>
  );
}
