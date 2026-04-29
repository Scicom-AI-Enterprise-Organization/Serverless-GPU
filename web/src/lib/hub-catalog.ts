import type { HubWorker } from "./types";

// The Worker Hub catalog. Only the vLLM worker container we publish to
// ECR Public is wired through — that image is what the gateway provisions
// when an endpoint is created. New workers should be added here once their
// images are in the same registry.
export const HUB_WORKERS: HubWorker[] = [
  {
    slug: "vllm",
    name: "vLLM",
    version: "v2.14.0",
    category: "Language",
    description:
      "Deploy OpenAI-compatible blazing-fast LLM endpoints powered by vLLM. Image pulled from public.ecr.aws/o6x1g6b0/serverlessgpu-pi-worker:latest.",
    publisher: "serverless-gpu",
    stars: 430,
    preconfiguredVars: 59,
    defaultModel: "Qwen/Qwen1.5-0.5B",
    iconLetter: "v",
    iconBg: "bg-violet-500/20 text-violet-300",
  },
];
