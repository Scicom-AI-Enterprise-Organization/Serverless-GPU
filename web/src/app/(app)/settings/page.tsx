import { redirect } from "next/navigation";
import { ConsoleTopbar } from "@/components/console/topbar";
import { getMe } from "@/lib/me";
import { ProfileForm } from "./profile-form";

export default async function SettingsPage() {
  const me = await getMe();
  if (!me) redirect("/login");

  return (
    <div className="flex min-h-full flex-col">
      <ConsoleTopbar crumbs={[{ label: "Settings" }]} username={me.username} />
      <div className="mx-auto w-full max-w-2xl px-6 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your profile. Username and email are tied to your account and can&apos;t be changed here.
          </p>
        </header>

        <ProfileForm
          username={me.username}
          email={me.email ?? ""}
          role={me.role}
        />
      </div>
    </div>
  );
}
