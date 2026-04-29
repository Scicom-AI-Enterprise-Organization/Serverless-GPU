"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Copy, Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { AppRecord } from "@/lib/types";
import { gateway } from "@/lib/gateway";

export function OverviewTab({ app }: { app: AppRecord }) {
  return (
    <div className="space-y-4">
      <RequestPanel app={app} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DetailCard app={app} />
        <ScaleStrategyCard app={app} />
      </div>
    </div>
  );
}

function RequestPanel({ app }: { app: AppRecord }) {
  const [reveal, setReveal] = useState(false);
  const { token, loading: tokenLoading } = useApiToken();
  const endpoint = `${gateway.baseUrl}/run/${app.app_id}`;

  // The visible / copyable forms of every snippet. Visible may be masked;
  // copy always pastes the real key so the user gets a working command.
  const visibleToken = reveal && token ? token : token ? maskToken(token) : "YOUR_API_KEY";
  const realToken = token ?? "YOUR_API_KEY";

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <CardTitle className="text-sm font-medium">Run a job</CardTitle>
          <span className="text-xs text-muted-foreground">
            Autoscales to meet demand.
          </span>
        </div>
        {token ? (
          <Button variant="outline" size="xs" onClick={() => setReveal((v) => !v)}>
            {reveal ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
            {reveal ? "Hide" : "Reveal"} key
          </Button>
        ) : !tokenLoading ? (
          <Link
            href="/login?next=/serverless"
            className="text-xs text-primary hover:underline"
          >
            Sign in to use your key
          </Link>
        ) : null}
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="curl">
          <TabsList variant="line" className="bg-transparent">
            <TabsTrigger value="curl">cURL</TabsTrigger>
            <TabsTrigger value="curl-poll">cURL (poll)</TabsTrigger>
            <TabsTrigger value="openai">OpenAI client</TabsTrigger>
          </TabsList>

          <TabsContent value="curl" className="mt-3 space-y-3">
            <p className="text-sm text-muted-foreground">
              Async — returns a <code className="font-mono">request_id</code> immediately, then poll{" "}
              <code className="font-mono">/result/&#123;id&#125;</code> for the output.
            </p>
            <CodeBlock
              displayCode={curlSnippet(endpoint, visibleToken)}
              copyCode={curlSnippet(endpoint, realToken)}
            />
            <DocsLink />
          </TabsContent>

          <TabsContent value="curl-poll" className="mt-3 space-y-3">
            <p className="text-sm text-muted-foreground">
              Sync — gateway polls internally and returns the completion in one call (60s ceiling). No request_id juggling.
            </p>
            <CodeBlock
              displayCode={curlPollSnippet(app.app_id, visibleToken)}
              copyCode={curlPollSnippet(app.app_id, realToken)}
            />
            <DocsLink />
          </TabsContent>

          <TabsContent value="openai" className="mt-3 space-y-3">
            <p className="text-sm text-muted-foreground">
              vLLM exposes an OpenAI-compatible API. Point any OpenAI client at the gateway and use the endpoint name as the <code className="font-mono">model</code>.
            </p>
            <CodeBlock
              displayCode={openaiSnippet(app.app_id, visibleToken)}
              copyCode={openaiSnippet(app.app_id, realToken)}
            />
            <DocsLink />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

function useApiToken() {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let abort = false;
    fetch("/api/auth/token", { cache: "no-store" })
      .then(async (r) => {
        if (abort) return;
        if (!r.ok) {
          setToken(null);
          return;
        }
        const body = (await r.json()) as { token?: string };
        setToken(body.token ?? null);
      })
      .catch(() => !abort && setToken(null))
      .finally(() => !abort && setLoading(false));
    return () => {
      abort = true;
    };
  }, []);
  return { token, loading };
}

function maskToken(t: string) {
  if (t.length <= 8) return "•".repeat(t.length);
  return `${t.slice(0, 4)}${"•".repeat(Math.max(8, t.length - 8))}${t.slice(-4)}`;
}

function CopyButton({ text }: { text: string }) {
  return (
    <Button
      variant="outline"
      size="icon-sm"
      onClick={() => {
        navigator.clipboard.writeText(text);
        toast.success("Copied");
      }}
    >
      <Copy className="h-3.5 w-3.5" />
    </Button>
  );
}

function CodeBlock({
  displayCode,
  copyCode,
}: {
  displayCode: string;
  copyCode?: string;
}) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground scrollbar-thin">
        {displayCode}
      </pre>
      <div className="absolute right-2 top-2">
        <CopyButton text={copyCode ?? displayCode} />
      </div>
    </div>
  );
}

function DocsLink() {
  return (
    <a
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
      href="#"
    >
      Job operations documentation
      <ArrowUpRight className="h-3 w-3" />
    </a>
  );
}

function curlSnippet(endpoint: string, token: string) {
  return `curl -X POST '${endpoint}' \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer ${token}' \\
  -d '{
    "prompt": "Hello, world",
    "max_tokens": 64
  }'`;
}

function curlPollSnippet(appId: string, token: string) {
  // Hits the OpenAI-compatible chat-completions endpoint, which polls
  // internally for up to 60s before returning the actual completion JSON.
  const base = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";
  return `curl -X POST '${base}/v1/chat/completions' \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer ${token}' \\
  -d '{
    "model": "${appId}",
    "messages": [{"role": "user", "content": "Hello, world"}]
  }'`;
}

function openaiSnippet(appId: string, token: string) {
  return `from openai import OpenAI

client = OpenAI(
    base_url="${process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080"}/v1",
    api_key="${token}",
)

resp = client.chat.completions.create(
    model="${appId}",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
)

for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="", flush=True)`;
}

function DetailCard({ app }: { app: AppRecord }) {
  return (
    <Card>
      <CardContent className="space-y-3 px-6 py-4 text-sm">
        <Row label="Endpoint ID" value={<code className="font-mono">{app.app_id}</code>} />
        <Row
          label="Created"
          value={new Date(app.created_at).toLocaleDateString("en-GB", {
            day: "2-digit",
            month: "short",
            year: "numeric",
          })}
        />
        <Row
          label="Hub listing"
          value={
            <span className="inline-flex items-center gap-1.5">
              <span className="flex h-5 w-5 items-center justify-center rounded bg-violet-500/20 text-[10px] font-semibold text-violet-300">
                v
              </span>
              vLLM
            </span>
          }
        />
        <Row label="GPU count" value="1" />
        <Row label="GPU types" value={<span className="font-mono">{app.gpu}</span>} />
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border/40 pb-2 last:border-b-0 last:pb-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground">{value}</span>
    </div>
  );
}

function ScaleStrategyCard({ app }: { app: AppRecord }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Scale strategy</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Row label="Active workers" value={<code className="font-mono">0</code>} />
        <Row
          label="Max workers"
          value={<code className="font-mono">{app.autoscaler.max_containers}</code>}
        />
        <Row
          label="Idle timeout"
          value={<code className="font-mono">{app.autoscaler.idle_timeout_s} s</code>}
        />
        <Row label="Auto scaling method" value="Queue delay" />
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          Scale up after <strong className="text-foreground">4</strong> seconds of queue delay.
          With zero workers initially, the first request adds one worker. Subsequent requests
          add workers only after waiting in the queue for 4 seconds.
        </p>
        <p className="text-xs text-muted-foreground">
          Assuming <strong className="text-foreground">1</strong> req/sec with{" "}
          <strong className="text-foreground">0.5</strong> s processing time.
        </p>
      </CardContent>
    </Card>
  );
}

