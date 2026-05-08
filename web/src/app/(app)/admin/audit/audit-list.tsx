"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AuditLogRecord } from "@/lib/types";

const ANY = "__any__";

function fmtTs(iso: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

export function AuditList({ initial }: { initial: AuditLogRecord[] }) {
  const [query, setQuery] = useState("");
  const [resourceType, setResourceType] = useState<string>(ANY);
  const [actor, setActor] = useState<string>(ANY);

  const resourceTypes = useMemo(() => {
    const s = new Set<string>();
    initial.forEach((e) => s.add(e.resource_type));
    return Array.from(s).sort();
  }, [initial]);

  const actors = useMemo(() => {
    const s = new Set<string>();
    initial.forEach((e) => s.add(e.actor_username));
    return Array.from(s).sort();
  }, [initial]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return initial.filter((e) => {
      if (resourceType !== ANY && e.resource_type !== resourceType) return false;
      if (actor !== ANY && e.actor_username !== actor) return false;
      if (!q) return true;
      const hay = [
        e.actor_username,
        e.action,
        e.resource_type,
        e.resource_id ?? "",
        e.resource_name ?? "",
        JSON.stringify(e.details ?? {}),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [initial, query, resourceType, actor]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search action, resource, actor…"
            className="pl-9"
          />
        </div>
        <Select value={actor} onValueChange={setActor}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Any actor" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>Any actor</SelectItem>
            {actors.map((a) => (
              <SelectItem key={a} value={a}>{a}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={resourceType} onValueChange={setResourceType}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Any resource" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>Any resource</SelectItem>
            {resourceTypes.map((t) => (
              <SelectItem key={t} value={t}>{t}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          {filtered.length} of {initial.length}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-left font-medium">When</th>
              <th className="px-4 py-2 text-left font-medium">Actor</th>
              <th className="px-4 py-2 text-left font-medium">Action</th>
              <th className="px-4 py-2 text-left font-medium">Resource</th>
              <th className="px-4 py-2 text-left font-medium">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.map((e) => (
              <tr key={e.id} className="hover:bg-muted/20">
                <td className="px-4 py-2 align-top">
                  <div className="text-xs">{fmtTs(e.created_at)}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {relativeTime(e.created_at)}
                  </div>
                </td>
                <td className="px-4 py-2 align-top font-medium">{e.actor_username}</td>
                <td className="px-4 py-2 align-top">
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                    {e.action}
                  </code>
                </td>
                <td className="px-4 py-2 align-top">
                  <div className="text-xs">
                    <span className="text-muted-foreground">{e.resource_type}</span>
                    {e.resource_name && <span> · {e.resource_name}</span>}
                  </div>
                  {e.resource_id && (
                    <div className="font-mono text-[11px] text-muted-foreground">
                      {e.resource_id}
                    </div>
                  )}
                </td>
                <td className="px-4 py-2 align-top">
                  {e.details ? (
                    <details className="max-w-md">
                      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                        view
                      </summary>
                      <pre className="mt-1 max-h-48 overflow-auto rounded-md border border-border bg-muted/40 p-2 font-mono text-[11px]">
                        {JSON.stringify(e.details, null, 2)}
                      </pre>
                    </details>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-12 text-center text-sm text-muted-foreground">
                  No matching events.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
