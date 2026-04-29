# Architecture

## TL;DR

```
┌─ control plane (in your k8s cluster) ──────────┐    ┌─ data plane (Prime Intellect) ─┐
│                                                 │    │                                 │
│   Client ─► Ingress ─► Gateway ─► Redis         │    │   PI pods (H100/A100/...)       │
│                          │                      │    │   ────────────────────          │
│                          ├─ Scheduler           │    │   each pod runs:                │
│                          ├─ Autoscaler  ────────┼────►   - vllm serve :8000            │
│                          ├─ Reconciler ────────┼────►   - worker-agent (Python)        │
│                          └─ /metrics            │    │     ↑ BRPOPs queue, streams     │
│                                                 │    │     ↓ heartbeats every 5s       │
└─────────────────────────────────────────────────┘    └─────────────────────────────────┘
                              ▲                                       │
                              └───────── HTTPS dial-back ─────────────┘
                              (worker registers, heartbeats, optional SSE pubsub via Redis)
```

The control plane is **stateless except for Redis** — gateway pods can scale
horizontally; Redis is the single source of truth for queues, worker registry,
results, and registration tokens. Workers run **on Prime Intellect** (or any
provider implementing the `Provider` ABC), provisioned on demand and torn down
when idle.

## Request lifecycle (cold path)

```
T=0s    Client POST /run/qwen
        ├─ Gateway: enqueue to queue:qwen, return request_id immediately

T=0.1s  Autoscaler tick (1Hz loop)
        ├─ samples queue_length=1, current_workers=0
        ├─ desired = ceil(1/30) = 1
        ├─ mints registration_token, calls provider.provision(env={token, app_id, ...})
        └─ stores register_token:{machine_id} in Redis with 5min TTL

T=0.2s  PrimeIntellectProvider
        └─ POST https://api.primeintellect.ai/api/v1/pods/
            { gpuType: H100_80GB, customTemplateId: ..., envVars: [...] }

T=15s   PI boots the pod, runs our custom-template image
        ├─ entrypoint-pi.sh launches vllm serve :8000 in background
        └─ launches worker-agent in foreground

T=20s   worker-agent
        ├─ POST gateway /workers/register {machine_id, app_id, token}
        ├─ gateway: validates token (one-shot), burns it, returns Redis URL
        └─ starts heartbeat loop (every 5s)

T=55s   vllm /health returns 200 (model loaded onto GPU)
        worker-agent BRPOPs queue:qwen, picks up the job

T=58s   worker-agent: POST localhost:8000/v1/completions, gets result
        SETEX result:{request_id} = {status: completed, output: ...}

T=58.1s Client (still polling): GET /result/{request_id} → 200
```

Cold start = ~60s for a 7B model. Warm requests = sub-3s.

## Hot path (subsequent requests)

```
T=0s    Client POST /run/qwen
T=0.01s Worker BRPOP gets it (already blocking on the queue)
T=0.02s Worker POST localhost:8000/v1/completions
T=2s    vLLM responds, worker SETEX result, client polls and gets it
```

## Streaming (SSE)

Direct HTTP gateway↔worker would require workers to expose inbound ports — so
we route streams through Redis pub/sub instead. Workers stay outbound-only.

```
Client           Gateway                Redis                  Worker
  │ POST /stream    │                                            │
  │────────────────►│                                            │
  │                 │ pubsub.subscribe(stream:{req_id})           │
  │                 │────────────────────►                       │
  │                 │ LPUSH queue (stream=true)                   │
  │                 │────────────────────►                       │
  │                 │                       BRPOP                │
  │                 │                       ────────────────────►│
  │                 │                                  vLLM stream
  │                 │                       PUBLISH              │
  │                 │                       ◄────────────────────│
  │                 │ ◄─── pubsub msg ────                       │
  │ ◄── data: ... ──│                                            │
  │                 │                       PUBLISH...           │
  │                 │                       ◄────────────────────│
  │ ◄── data: ... ──│                                            │
  │                 │                       PUBLISH {done:true}  │
  │ ◄── data: done ─│                                            │
```

**Subscribe-before-enqueue** — gateway subscribes to `stream:{request_id}`
*before* LPUSHing the job. Without this, a fast worker could publish before
the gateway is listening → client misses chunks.

**Cancellation** — when client disconnects, FastAPI cancels the SSE generator
task → finally block sets `cancel:{request_id}` in Redis with 60s TTL. Worker
checks this between every chunk publish; on hit, breaks out, publishes a final
`{cancelled: true, done: true}` chunk, writes `result` with `status=cancelled`.
Saves GPU cycles when clients hang up.

## Components

### Gateway (`gateway/gateway/`)

| File | Role |
|---|---|
| `main.py` | FastAPI app with all routes, lifespan starts autoscaler + reconciler if `AUTOSCALER=1` |
| `auth.py` | `Authorization: Bearer` validation against `GATEWAY_API_KEYS` env (comma-separated, multi-key supports zero-downtime rotation) |
| `metrics.py` | `prometheus_client` registry, /metrics endpoint, point-in-time gauges sampled from Redis |
| `provider.py` | `Provider` ABC (`provision`/`terminate`/`list_machines`/`shutdown`) + `FakeProvider` (in-process asyncio tasks for tests/local dev) |
| `pi_provider.py` | `PrimeIntellectProvider` — HTTP calls against PI's REST API, GPU enum mapping, name-prefix filtering for multi-tenant safety |
| `autoscaler.py` | 1Hz loop: per-app queue_length → desired_containers → provider.provision/terminate; mints one-shot registration tokens |
| `reconciler.py` | 5s loop: trusts the cloud API as source of truth; SREMs Redis entries for machines the provider doesn't know about; logs orphans (provider has it, we don't) |

Routes:
- `POST /apps` (auth) — create
- `GET /apps` (auth) — list
- `GET /apps/:id` (auth) — show
- `DELETE /apps/:id` (auth) — drain workers + clean up Redis
- `POST /run/:id` (auth) — async enqueue, returns request_id
- `POST /stream/:id` (auth) — SSE
- `GET /result/:id` (auth) — poll
- `POST /workers/register` — gated by one-shot registration token
- `POST /workers/heartbeat` — extends worker TTL, returns drain flag if app deleted
- `GET /health` — liveness (always 200 if process up)
- `GET /ready` — readiness (200 only if Redis pingable; 503 otherwise so k8s depools)
- `GET /metrics` — Prometheus

### Worker (`worker-agent/worker_agent/`)

Single Python module. On boot:
1. Read env (APP_ID, GATEWAY_URL, REGISTRATION_TOKEN, MODEL_ID, WORKER_MODE)
2. POST `/workers/register` → get back Redis URL
3. Spawn heartbeat task (every 5s; sets drain flag receiver)
4. BRPOP `queue:{app_id}` loop

Per-job:
- Unary: `asyncio.wait_for(handle(...), timeout=timeout_s)` → SETEX result
- Streaming: deadline-tracked `gen.__anext__()` interleaved with cancel-key check + pubsub PUBLISH

Two modes via `WORKER_MODE` env:
- `fake` — canned responses for local dev / tests
- `vllm` — POST to localhost:8000/v1/completions (or stream via SSE)

### SDK + CLI (`sdk/serverlessgpu/`)

- `decorators.py` — `@endpoint(model, gpu, autoscaler=...)` attaches an `EndpointSpec` to the function
- `cli.py` — `deploy / run / stream / list / show / delete / pi-check`. Auto-attaches `Authorization: Bearer $SERVERLESSGPU_API_KEY` if set.

## Redis key schema

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `app:{app_id}` | string (JSON) | none | App spec record |
| `apps:index` | set | none | All deployed app names (autoscaler iterates this) |
| `queue:{app_id}` | list | none | Job queue (LPUSH from gateway, BRPOP by worker) |
| `result:{request_id}` | string (JSON) | 3600s | Result payload, status=pending/completed/timeout/cancelled |
| `app:{app_id}:last_request_ts` | string | none | Float timestamp of last `/run` — autoscaler uses for idle scale-down |
| `worker:{machine_id}` | string (JSON) | 30s | Worker state; TTL refreshed by heartbeat. Expires → reconciler GCs |
| `worker_index:{app_id}` | set | none | All machine_ids assigned to this app |
| `worker:{machine_id}:drain` | string | 600s | Set on app delete or scale-down → worker exits at next heartbeat |
| `register_token:{machine_id}` | string | 300s | One-shot registration token. Burned on /workers/register. Pod must register within 5min. |
| `cancel:{request_id}` | string | 60s | Streaming-cancel flag. Worker checks between PUBLISHes. |

## Provider abstraction

```python
class Provider(ABC):
    name: str

    @abstractmethod
    async def provision(self, app_id: str, model: str, gpu: str, env: dict) -> str:
        """Spawn a worker. Return machine_id immediately — worker registers async."""

    @abstractmethod
    async def terminate(self, machine_id: str) -> None: ...

    @abstractmethod
    async def list_machines(self) -> list[str]:
        """Authoritative source of liveness. Reconciler reads this every 5s."""

    async def shutdown(self) -> None: ...
```

Two concrete providers ship today:
- `FakeProvider` — runs workers as in-process asyncio tasks. For tests + local dev.
- `PrimeIntellectProvider` — HTTP calls against PI's REST API.

Adding a new cloud (RunPod, Modal, Lambda Labs, generic SSH) means writing one
class against this 4-method interface. Nothing else in the codebase changes.

## Why not direct HTTP gateway↔worker?

Modal / RunPod do this. We could too, but Redis pub/sub keeps workers
**outbound-only**. PI pods may be behind NAT / not have stable IPs. With pub/sub,
workers never need an inbound port and the gateway never needs to know IPs.
Cost: an extra hop for streaming tokens. At per-token latency this is invisible
(<1ms in our tests, well under a network RTT to a US datacenter).

## Why not Postgres for app definitions?

For V0, Redis with AOF + a PVC is durable enough — `compose down` (without `-v`)
preserves apps; the Helm chart mounts a PV on the Redis StatefulSet. Postgres
becomes worthwhile when we need: multi-tenant isolation, audit trails, complex
queries. Beta9 split it that way; we'll likely follow when those needs surface.

## Why not Tailscale / overlay network?

All worker → gateway communication is plain HTTPS with bearer-token auth
(registration token at `/workers/register`, machine_id heartbeats, never any
secrets in flight beyond the one-shot token). PI pods get public IPs anyway.
This is the same model RunPod uses for its workers — nothing exotic needed.
