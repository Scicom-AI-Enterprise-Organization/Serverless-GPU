"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { deployEndpoint } from "../actions";

// vLLM is what the live RunPod template runs. SGLang is a placeholder for a
// future template — keep it disabled so the option is visible but inert.
const FRAMEWORKS = [
  { value: "vllm", label: "vLLM", available: true },
  { value: "sglang", label: "SGLang (coming soon)", available: false },
] as const;

// `vramGb` and `fits` hints are surfaced so users can size the GPU against
// their model — vLLM will OOM on load if weights + KV cache exceed VRAM.
const GPU_CHOICES = [
  { value: "rtx3090", label: "RTX 3090 (24 GB)", fits: "up to ~7B FP16 / ~13B 4-bit" },
  { value: "A10-24GB", label: "A10G (24 GB)", fits: "up to ~7B FP16 / ~13B 4-bit" },
  { value: "L40S-48GB", label: "L40S (48 GB)", fits: "up to ~13B FP16 / ~30B 4-bit" },
  { value: "A100-80GB", label: "A100 (80 GB)", fits: "up to ~30B FP16 / ~70B 4-bit" },
  { value: "H100-80GB", label: "H100 (80 GB)", fits: "up to ~30B FP16 / ~70B 4-bit" },
];

const MAX_WORKERS = 1;

export function InferenceForm() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [framework, setFramework] = useState("vllm");
  const [name, setName] = useState(suggestName());
  const [model, setModel] = useState("");
  const [gpu, setGpu] = useState(GPU_CHOICES[0].value);
  const [idleInput, setIdleInput] = useState("");
  const [alwaysOn, setAlwaysOn] = useState(true);

  const parsedIdle = Number.parseInt(idleInput, 10);
  const idleInvalid =
    !alwaysOn && (!Number.isFinite(parsedIdle) || parsedIdle < 1 || parsedIdle > 86400);

  function submit() {
    if (!name.trim() || !model.trim()) {
      toast.error("Endpoint name and model name are required.");
      return;
    }
    if (idleInvalid) {
      toast.error(
        "Enter a positive idle timeout in seconds, or tick 'No idle timeout'.",
      );
      return;
    }
    startTransition(async () => {
      const res = await deployEndpoint({
        name: slugify(name),
        model: model.trim(),
        gpu,
        autoscaler: {
          max_containers: MAX_WORKERS,
          idle_timeout_s: alwaysOn ? 0 : parsedIdle,
        },
      });
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      toast.success(`Endpoint ${res.app_id} created`);
      router.push(`/serverless/${encodeURIComponent(res.app_id)}`);
    });
  }

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Create inference endpoint</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a framework and a model. The endpoint scales to zero when idle.
        </p>
      </div>

      <div className="space-y-5 rounded-xl border border-border bg-card p-6">
        <Field
          label="Inference framework"
          hint="Choose the inference server. Only vLLM is enabled today."
        >
          <Select value={framework} onValueChange={setFramework}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FRAMEWORKS.map((f) => (
                <SelectItem key={f.value} value={f.value} disabled={!f.available}>
                  {f.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Endpoint name" required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-endpoint"
              className="bg-muted"
            />
          </Field>
          <Field
            label="GPU"
            hint={GPU_CHOICES.find((g) => g.value === gpu)?.fits}
          >
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

        <Field label="Model" hint="Hugging Face repo (e.g. Qwen/Qwen2.5-7B-Instruct)" required>
          <Input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Qwen/Qwen2.5-7B-Instruct"
            className="bg-muted/50"
          />
        </Field>

        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Pick a GPU with enough VRAM for your model. vLLM will fail to load if the
            weights plus KV cache exceed GPU memory.
          </span>
        </div>

        <Field
          label="Idle timeout (s)"
          hint="Worker is torn down after this many seconds with no traffic."
        >
          <div className="flex flex-col gap-2">
            <Input
              type="text"
              inputMode="numeric"
              value={idleInput}
              onChange={(e) => {
                setIdleInput(e.target.value);
                if (e.target.value.trim() !== "") setAlwaysOn(false);
              }}
              placeholder={alwaysOn ? "Always-on (no timeout)" : "e.g. 300"}
              aria-invalid={idleInvalid}
            />
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={alwaysOn}
                onCheckedChange={(v) => {
                  const next = v === true;
                  setAlwaysOn(next);
                  if (next) setIdleInput("");
                }}
              />
              <span>No idle timeout (keep worker up forever)</span>
            </label>
          </div>
        </Field>

        <p className="text-xs text-muted-foreground">
          Max workers is fixed at <span className="font-medium text-foreground">1</span> for now.
        </p>
      </div>

      <div className="mt-5 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={() => router.push("/serverless")} disabled={pending}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={pending || idleInvalid}>
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          Create endpoint
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
        {required && <span className="ml-1 text-destructive">*</span>}
      </Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}

function suggestName() {
  const suffix = Math.floor(Math.random() * 9000 + 1000);
  return `endpoint-${suffix}`;
}
