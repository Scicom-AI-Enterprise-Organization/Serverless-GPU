import { ConsoleTopbar } from "@/components/console/topbar";
import { NoAccessAlert } from "@/components/no-access-alert";
import { currentUsername } from "@/lib/current-user";
import { gateway } from "@/lib/gateway";
import { getMe } from "@/lib/me";
import { BenchmarkForm } from "./benchmark-form";

export default async function NewBenchmarkPage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string }>;
}) {
  const me = await getMe();
  const noAccess = me?.role === "user";
  const username = await currentUsername();
  const { from } = await searchParams;

  let initialName: string | undefined;
  let initialYaml: string | undefined;
  let initialProviderId: string | null | undefined;
  if (from && !noAccess) {
    try {
      const src = await gateway.getBenchmark(from);
      initialName = `${src.name}-copy`;
      initialYaml = src.config_yaml;
      initialProviderId = src.provider_id ?? null;
    } catch {
      // ignore — fall back to default empty form
    }
  }

  return (
    <>
      <ConsoleTopbar
        crumbs={[
          { label: "Benchmark", href: "/benchmark" },
          { label: "New benchmark" },
        ]}
        username={username}
      />
      <div className="px-6 py-6 lg:px-10 lg:py-8">
        {noAccess ? (
          <NoAccessAlert />
        ) : (
          <BenchmarkForm
            initialName={initialName}
            initialYaml={initialYaml}
            initialProviderId={initialProviderId}
          />
        )}
      </div>
    </>
  );
}
