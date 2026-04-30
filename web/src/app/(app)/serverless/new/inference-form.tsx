"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
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

// Common vLLM engine args. Defaults are conservative — users can override.
// Reference: https://docs.vllm.ai/en/stable/configuration/engine_args/
const DEFAULT_VLLM_ARGS = {
  max_model_len: "",
  gpu_memory_utilization: "0.9",
  dtype: "auto",
  max_num_seqs: "",
  tensor_parallel_size: "1",
  extra: "",
};

const DTYPE_CHOICES = ["auto", "float16", "bfloat16", "float32"] as const;

function buildVllmArgs(v: typeof DEFAULT_VLLM_ARGS): string {
  const parts: string[] = [];
  if (v.max_model_len.trim()) parts.push(`--max-model-len ${v.max_model_len.trim()}`);
  if (v.gpu_memory_utilization.trim() && v.gpu_memory_utilization.trim() !== "0.9") {
    parts.push(`--gpu-memory-utilization ${v.gpu_memory_utilization.trim()}`);
  }
  if (v.dtype && v.dtype !== "auto") parts.push(`--dtype ${v.dtype}`);
  if (v.max_num_seqs.trim()) parts.push(`--max-num-seqs ${v.max_num_seqs.trim()}`);
  if (v.tensor_parallel_size.trim() && v.tensor_parallel_size.trim() !== "1") {
    parts.push(`--tensor-parallel-size ${v.tensor_parallel_size.trim()}`);
  }
  if (v.extra.trim()) parts.push(v.extra.trim());
  return parts.join(" ");
}

export function InferenceForm() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [framework, setFramework] = useState("vllm");
  const [name, setName] = useState(suggestName());
  const [model, setModel] = useState("");
  const [gpu, setGpu] = useState(GPU_CHOICES[0].value);
  const [idleInput, setIdleInput] = useState("");
  const [alwaysOn, setAlwaysOn] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [vllm, setVllm] = useState({ ...DEFAULT_VLLM_ARGS });

  const gpuMemInvalid = (() => {
    const s = vllm.gpu_memory_utilization.trim();
    if (!s) return false;
    const n = Number.parseFloat(s);
    return !Number.isFinite(n) || n <= 0 || n > 1;
  })();
  const intFieldInvalid = (s: string) => {
    if (!s.trim()) return false;
    return !/^\d+$/.test(s.trim()) || Number.parseInt(s.trim(), 10) < 1;
  };
  const advancedInvalid =
    gpuMemInvalid ||
    intFieldInvalid(vllm.max_model_len) ||
    intFieldInvalid(vllm.max_num_seqs) ||
    intFieldInvalid(vllm.tensor_parallel_size);

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
    if (advancedInvalid) {
      toast.error("Fix the invalid values in Advanced options.");
      return;
    }
    const vllmArgs = buildVllmArgs(vllm);
    startTransition(async () => {
      const res = await deployEndpoint({
        name: slugify(name),
        model: model.trim(),
        gpu,
        autoscaler: {
          max_containers: MAX_WORKERS,
          idle_timeout_s: alwaysOn ? 0 : parsedIdle,
        },
        vllm_args: vllmArgs,
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

        <div className="border-t border-border pt-4">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex w-full items-center gap-1.5 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground"
          >
            {advancedOpen ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Advanced options (vLLM engine args)
          </button>
          {advancedOpen && (
            <div className="mt-4 space-y-4">
              <p className="text-xs text-muted-foreground">
                Defaults are sensible for most models. Override only when you know you need to.
                See{" "}
                <a
                  href="https://docs.vllm.ai/en/stable/configuration/engine_args/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:text-foreground"
                >
                  vLLM engine args
                </a>
                .
              </p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field
                  label="max-model-len"
                  hint="Context window in tokens. Empty = model's default."
                >
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={vllm.max_model_len}
                    onChange={(e) =>
                      setVllm((v) => ({ ...v, max_model_len: e.target.value }))
                    }
                    placeholder="e.g. 4096"
                    aria-invalid={intFieldInvalid(vllm.max_model_len)}
                  />
                </Field>
                <Field
                  label="gpu-memory-utilization"
                  hint="Fraction of VRAM vLLM may use (0–1). Default 0.9."
                >
                  <Input
                    type="text"
                    inputMode="decimal"
                    value={vllm.gpu_memory_utilization}
                    onChange={(e) =>
                      setVllm((v) => ({ ...v, gpu_memory_utilization: e.target.value }))
                    }
                    placeholder="0.9"
                    aria-invalid={gpuMemInvalid}
                  />
                </Field>
                <Field label="dtype" hint="Weight precision.">
                  <Select
                    value={vllm.dtype}
                    onValueChange={(val) => setVllm((v) => ({ ...v, dtype: val }))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {DTYPE_CHOICES.map((d) => (
                        <SelectItem key={d} value={d}>
                          {d}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field
                  label="max-num-seqs"
                  hint="Max concurrent sequences. Empty = vLLM default."
                >
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={vllm.max_num_seqs}
                    onChange={(e) =>
                      setVllm((v) => ({ ...v, max_num_seqs: e.target.value }))
                    }
                    placeholder="e.g. 256"
                    aria-invalid={intFieldInvalid(vllm.max_num_seqs)}
                  />
                </Field>
                <Field
                  label="tensor-parallel-size"
                  hint="Number of GPUs for tensor parallelism. Default 1."
                >
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={vllm.tensor_parallel_size}
                    onChange={(e) =>
                      setVllm((v) => ({ ...v, tensor_parallel_size: e.target.value }))
                    }
                    placeholder="1"
                    aria-invalid={intFieldInvalid(vllm.tensor_parallel_size)}
                  />
                </Field>
              </div>
              <Field
                label="Extra args (raw)"
                hint="Appended verbatim to the vllm serve command. e.g. --enforce-eager --quantization awq"
              >
                <textarea
                  value={vllm.extra}
                  onChange={(e) => setVllm((v) => ({ ...v, extra: e.target.value }))}
                  placeholder="--enforce-eager"
                  rows={2}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                />
              </Field>
              {buildVllmArgs(vllm) && (
                <div className="rounded-md bg-muted/50 px-3 py-2 text-xs">
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                    Final command
                  </div>
                  <code className="break-words font-mono text-foreground">
                    vllm serve {`<model>`} {buildVllmArgs(vllm)}
                  </code>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mt-5 flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={() => router.push("/serverless")} disabled={pending}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={pending || idleInvalid || advancedInvalid}>
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
