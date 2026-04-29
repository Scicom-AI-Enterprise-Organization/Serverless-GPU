import Link from "next/link";
import { ArrowRight, KeyRound, Zap } from "lucide-react";
import { cookies } from "next/headers";
import { Button } from "@/components/ui/button";
import { TOKEN_COOKIE } from "@/lib/auth-cookie";

// Drop a looping subway-surfers clip at /public/videos/subway.mp4 (mp4)
// and optionally /public/videos/subway.webm. Without it the page falls
// back to the static gradient.
const VIDEO_SRC = "/videos/subway.mp4";

export default async function Home() {
  const jar = await cookies();
  const signedIn = !!jar.get(TOKEN_COOKIE)?.value;

  return (
    <div className="relative isolate min-h-screen overflow-hidden bg-background text-foreground">
      {/* Background video — fills the viewport, blurred, dimmed. */}
      <video
        className="absolute inset-0 -z-10 h-full w-full object-cover blur-md scale-110 opacity-40 saturate-150"
        autoPlay
        muted
        loop
        playsInline
        // poster hides the broken-video icon if the file is missing
        poster="/images/scicom-logo.png"
      >
        <source src={VIDEO_SRC} type="video/mp4" />
      </video>

      {/* Tint over the video so foreground text stays readable in both themes */}
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-gradient-to-b from-background/30 via-background/60 to-background"
      />

      <main className="relative mx-auto flex min-h-screen max-w-5xl flex-col items-center justify-center px-6 py-20 text-center">
        <span className="rounded-full border border-border bg-background/60 px-3 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground backdrop-blur">
          Now available
        </span>
        <h1 className="mt-6 text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
          Run inference while bouldering.
        </h1>
        <p className="mt-6 max-w-2xl text-balance text-lg text-muted-foreground">
          Deploy vLLM endpoints in one click. Autoscale GPU workers across clouds.
          Pay per second of compute. No idle pods, no cold-start gymnastics.
        </p>

        <div className="mt-10 flex flex-col gap-3 sm:flex-row">
          <Button asChild size="lg">
            <Link href={signedIn ? "/serverless" : "/register"}>
              {signedIn ? "Open console" : "Get started"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </Button>
          {!signedIn && (
            <Button asChild variant="outline" size="lg">
              <Link href="/login">Sign in</Link>
            </Button>
          )}
        </div>

        <div className="mt-16 grid w-full max-w-3xl grid-cols-1 gap-4 sm:grid-cols-3">
          <Tile
            icon={<Zap className="h-4 w-4" />}
            title="Cold start to running"
            value="~60s"
          />
          <Tile
            icon={<KeyRound className="h-4 w-4" />}
            title="Auth"
            value="Per-user keys"
          />
          <Tile
            icon={<ArrowRight className="h-4 w-4" />}
            title="API surface"
            value="OpenAI-compat"
          />
        </div>
      </main>
    </div>
  );
}

function Tile({
  icon,
  title,
  value,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-background/60 px-4 py-3 text-left backdrop-blur">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
        {icon}
        {title}
      </div>
      <div className="mt-1 text-lg font-semibold text-foreground">{value}</div>
    </div>
  );
}
