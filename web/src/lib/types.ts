// Mirrors gateway/main.py Pydantic models. Keep field names + defaults in sync.

export type AutoscalerSpec = {
  max_containers: number;
  tasks_per_container: number;
  idle_timeout_s: number;
};

export type AppRecord = {
  app_id: string;
  name: string;
  model: string;
  gpu: string;
  autoscaler: AutoscalerSpec;
  cpu: number;
  memory: string;
  request_timeout_s: number;
  created_at: string;
  owner: string;
};

export type CreateAppRequest = {
  name: string;
  model: string;
  gpu: string;
  autoscaler?: Partial<AutoscalerSpec>;
  cpu?: number;
  memory?: string;
  request_timeout_s?: number;
};

export type CreateAppResponse = {
  app_id: string;
  url: string;
};

// Worker types are not exposed by the gateway directly today — mock data
// shapes matching what the dashboard renders.
export type WorkerStatus = "idle" | "running" | "initializing" | "throttled" | "down";

export type WorkerRow = {
  id: string;
  status: WorkerStatus;
  region_code: string;   // "IN", "US", "DE"
  region: string;        // "AP-IN-1"
  gpu: string;           // "H100 SXM"
  vcpus: number;
  ram: string;           // "251 GB"
  release: string;       // "Latest"
  count: number;
};

export type RequestRow = {
  id: string;
  status: "in queue" | "in progress" | "completed" | "failed";
  duration_ms: number;
  delay_ms?: number;
  cost_usd?: number;
};

export type Me = {
  user_id: number;
  username: string;
};

// Worker Hub catalog item — only vLLM today, but typed so we can add more.
export type HubWorker = {
  slug: string;
  name: string;
  version: string;
  category: "Language" | "Image" | "Video" | "Audio" | "Embedding";
  description: string;
  publisher: string;
  stars: number;
  preconfiguredVars: number;
  defaultModel: string;
  iconLetter: string;     // single-letter logo fallback
  iconBg: string;         // tailwind bg class
};
