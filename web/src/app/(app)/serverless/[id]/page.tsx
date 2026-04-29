import { notFound } from "next/navigation";
import { ConsoleTopbar } from "@/components/console/topbar";
import { gateway } from "@/lib/gateway";
import { currentUsername } from "@/lib/current-user";
import { EndpointDetail } from "./endpoint-detail";

export default async function EndpointPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const username = await currentUsername();
  let app;
  try {
    app = await gateway.getApp(id);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("404")) notFound();
    return (
      <div className="flex h-full flex-col">
        <ConsoleTopbar
          crumbs={[{ label: "Serverless", href: "/serverless" }, { label: id }]}
          username={username}
        />
        <div className="flex-1 px-6 py-8">
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Couldn&apos;t reach the gateway: {msg}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <ConsoleTopbar
        crumbs={[{ label: "Serverless", href: "/serverless" }, { label: app.name }]}
        username={username}
      />
      <EndpointDetail app={app} />
    </div>
  );
}
