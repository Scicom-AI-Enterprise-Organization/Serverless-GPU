"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, Loader2, Settings2 } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { HubWorker } from "@/lib/types";
import { deployEndpoint } from "../actions";

// Values mirror what the gateway's RunPod provider filters on. rtx3090 is
// the cheapest option that still has CUDA 13 host coverage.
const GPU_CHOICES = [
  { value: "rtx3090", label: "RTX 3090 (24 GB)" },
  { value: "A10-24GB", label: "A10G (24 GB)" },
  { value: "L40S-48GB", label: "L40S (48 GB)" },
  { value: "A100-80GB", label: "A100 (80 GB)" },
  { value: "H100-80GB", label: "H100 (80 GB)" },
];

export function DeployModal({
  worker,
  onClose,
}: {
  worker: HubWorker | null;
  onClose: () => void;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [gpu, setGpu] = useState(GPU_CHOICES[0].value);
  const [maxWorkers, setMaxWorkers] = useState(3);
  const [idleTimeoutS, setIdleTimeoutS] = useState(300);
  const [advanced, setAdvanced] = useState(false);

  // Reset the form whenever the worker prop changes (open/close).
  useEffect(() => {
    if (worker) {
      setName(suggestEndpointName(worker.name));
      setModel(worker.defaultModel);
      setGpu(GPU_CHOICES[0].value);
      setMaxWorkers(3);
      setIdleTimeoutS(300);
      setAdvanced(false);
    }
  }, [worker]);

  if (!worker) return null;

  function submit() {
    if (!name.trim() || !model.trim()) {
      toast.error("Endpoint name and model name are required.");
      return;
    }
    startTransition(async () => {
      const res = await deployEndpoint({
        name: slugify(name),
        model: model.trim(),
        gpu,
        autoscaler: { max_containers: maxWorkers, idle_timeout_s: idleTimeoutS },
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      toast.success(`Endpoint ${res.app_id} created`);
      onClose();
      router.push(`/serverless/${encodeURIComponent(res.app_id)}`);
    });
  }

  return (
    <Dialog open={!!worker} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Deploy {worker.name}</DialogTitle>
          <DialogDescription>
            Deploy this repo with the following configuration.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <section>
            <h4 className="text-sm font-medium">Deployment type</h4>
            <p className="text-sm text-muted-foreground">
              Deploy this repo as a serverless endpoint with autoscaling.
            </p>
          </section>

          <div className="rounded-lg border border-border bg-muted/40 p-4">
            <div className="flex items-start gap-3">
              <div className={`flex h-10 w-10 items-center justify-center rounded-lg font-semibold ${worker.iconBg}`}>
                {worker.iconLetter}
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{worker.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{worker.version}</span>
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                  <span>endpoint</span>
                  <span>·</span>
                  <span>{worker.preconfiguredVars} pre-configured variables</span>
                </div>
              </div>
              <div className="text-xs text-muted-foreground">{worker.publisher}</div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Endpoint name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-endpoint"
              />
            </Field>
            <Field label="GPU">
              <Select value={gpu} onValueChange={setGpu}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {GPU_CHOICES.map((g) => (
                    <SelectItem key={g.value} value={g.value}>
                      {g.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>

          <Field label="Model name" hint="Hugging Face repo (e.g. Qwen/Qwen2.5-7B-Instruct)">
            <Input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="Qwen/Qwen2.5-7B-Instruct"
            />
          </Field>

          <button
            type="button"
            onClick={() => setAdvanced((v) => !v)}
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform ${advanced ? "" : "-rotate-90"}`}
            />
            <Settings2 className="h-3.5 w-3.5" />
            Advanced
          </button>

          {advanced && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Max workers" hint="Autoscaler will provision up to this many containers.">
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={maxWorkers}
                  onChange={(e) => setMaxWorkers(Math.max(1, Number(e.target.value)))}
                />
              </Field>
              <Field label="Idle timeout (s)" hint="Terminate workers after this many seconds of no traffic.">
                <Input
                  type="number"
                  min={0}
                  max={86400}
                  value={idleTimeoutS}
                  onChange={(e) => setIdleTimeoutS(Math.max(0, Number(e.target.value)))}
                />
              </Field>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={pending}>
            {pending && <Loader2 className="h-4 w-4 animate-spin" />}
            Create endpoint
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}

function suggestEndpointName(workerName: string) {
  const base = slugify(workerName);
  const suffix = Math.floor(Math.random() * 9000 + 1000);
  return `${base}-${suffix}`;
}
