"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Boxes, Loader2, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AppRecord } from "@/lib/types";
import { avatarFor } from "@/lib/avatar";
import { cn } from "@/lib/utils";
import { deleteEndpoint } from "./actions";

export function EndpointGrid({ apps }: { apps: AppRecord[] }) {
  const [confirm, setConfirm] = useState<AppRecord | null>(null);

  if (apps.length === 0) {
    return (
      <div className="grid place-items-center rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
          <Boxes className="h-5 w-5" />
        </div>
        <h2 className="mt-4 text-base font-medium">No endpoints yet</h2>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">
          Spin up your first inference endpoint.
        </p>
        <Button asChild className="mt-6">
          <Link href="/serverless/new">
            <Plus className="h-4 w-4" />
            New endpoint
          </Link>
        </Button>
      </div>
    );
  }
  return (
    <>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {apps.map((app) => (
          <EndpointCard key={app.app_id} app={app} onDelete={() => setConfirm(app)} />
        ))}
      </div>

      <DeleteDialog target={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}

function EndpointCard({ app, onDelete }: { app: AppRecord; onDelete: () => void }) {
  const avatar = avatarFor(app.name);
  return (
    <Link
      href={`/serverless/${encodeURIComponent(app.app_id)}`}
      className="group block rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-card/80"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "flex h-10 w-10 items-center justify-center rounded-md text-base font-semibold",
            avatar.bg,
            avatar.text,
          )}>
            {avatar.letter}
          </div>
          <div>
            <div className="font-medium text-foreground">{app.name}</div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono">{app.model}</span>
              {app.owner && (
                <>
                  <span>·</span>
                  <span>by {app.owner}</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-status-active/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-status-active">
            Ready
          </span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="-mr-1 text-muted-foreground hover:text-foreground"
                aria-label="Actions"
                onClick={(e) => {
                  // Stop the click bubbling up to the card-level <Link>; we
                  // want the menu to open, not navigate.
                  e.preventDefault();
                  e.stopPropagation();
                }}
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
              <DropdownMenuItem
                variant="destructive"
                onSelect={(e) => {
                  e.preventDefault();
                  onDelete();
                }}
              >
                <Trash2 className="h-4 w-4" />
                Delete endpoint
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-xs">
        <Stat label="GPU" value={app.gpu} />
        <Stat label="Max workers" value={String(app.autoscaler.max_containers)} />
        <Stat label="Created" value={new Date(app.created_at).toLocaleDateString()} />
      </dl>
    </Link>
  );
}

function DeleteDialog({
  target,
  onClose,
}: {
  target: AppRecord | null;
  onClose: () => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function handleDelete() {
    if (!target) return;
    startTransition(async () => {
      const res = await deleteEndpoint(target.app_id);
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      toast.success(`Deleted ${target.app_id}`);
      onClose();
      router.refresh();
    });
  }

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {target?.name}?</DialogTitle>
          <DialogDescription>
            All workers will be drained and the queue cleared. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleDelete} disabled={pending}>
            {pending && <Loader2 className="h-4 w-4 animate-spin" />}
            Delete endpoint
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/40 px-2 py-1.5">
      <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-medium text-foreground">{value}</dd>
    </div>
  );
}
