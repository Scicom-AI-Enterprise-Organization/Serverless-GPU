import { ConsoleTopbar } from "@/components/console/topbar";
import { currentUsername } from "@/lib/current-user";
import { HubCatalog } from "./hub-catalog";

export default async function NewEndpointPage() {
  const username = await currentUsername();
  return (
    <div className="flex h-full flex-col">
      <ConsoleTopbar
        crumbs={[
          { label: "Serverless", href: "/serverless" },
          { label: "Deploy Serverless endpoint" },
        ]}
        username={username}
      />
      <div className="flex-1 overflow-y-auto px-6 py-6 lg:px-10 lg:py-8 scrollbar-thin">
        <HubCatalog />
      </div>
    </div>
  );
}
