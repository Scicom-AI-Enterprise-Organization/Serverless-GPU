# Serverless-GPU

A serverless GPU inference platform. Deploy a model with a Python decorator,
get an autoscaling HTTP endpoint backed by GPU pods on **Prime Intellect**.
Scales to zero when idle, autoscales up under load.

```python
from serverlessgpu import endpoint, QueueDepthAutoscaler

@endpoint(
    model="Qwen/Qwen2.5-7B-Instruct",
    gpu="H100",
    autoscaler=QueueDepthAutoscaler(max_containers=3, tasks_per_container=30, idle_timeout_s=300),
)
def qwen():
    pass  # vLLM serves the model — no body needed
```

```bash
serverlessgpu deploy app.py:qwen
serverlessgpu run    qwen --payload '{"prompt": "hello"}'
serverlessgpu stream qwen --payload '{"prompt": "tell me a story"}'   # SSE token streaming
```

## What's in here

- **Control plane** (Python, FastAPI + redis-py + asyncio) — gateway, scheduler, autoscaler, reconciler, provider abstraction, Prometheus metrics
- **Worker agent** (Python, asyncio) — BRPOPs jobs, runs vLLM, publishes streaming tokens via Redis pub/sub
- **SDK + CLI** (`serverlessgpu` Python package) — `@endpoint` decorator + deploy/run/stream/list/delete/show/pi-check
- **K8s Helm chart** (`deploy/helm/`) — gateway Deployment + Redis StatefulSet + Ingress with SSE-friendly proxy settings
- **Worker image** (`worker-agent/Dockerfile.pi`) — vLLM + worker-agent in one container, baked as a PI custom template
- **Grafana dashboard** (`deploy/grafana/`) — 8 panels matching the metrics

## 5-minute quickstart (no real GPU)

```bash
make install              # uv venv + install all 3 packages editable + pytest
make test                 # 24 tests pass in ~0.5s

cp .env.example .env
docker compose up --build # gateway + redis + 1 fake worker (no real GPU)

# new terminal
serverlessgpu deploy sdk/examples/qwen.py:qwen
serverlessgpu run qwen --payload '{"prompt": "hello"}'
```

The fake worker emits canned responses — proves the full control-plane round-trip
without burning GPU money. Phase 1 swaps it for a real PI worker.

## Going further

| Doc | What's in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | ASCII flow diagram, component-by-component walk-through, Redis key schema, design rationale |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Three deploy paths (local fake / local + real PI / k8s + helm), copy-pasteable |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Auth, health probes, timeouts, observability, tear-down |
| [deploy/helm/serverlessgpu/README.md](deploy/helm/serverlessgpu/README.md) | k8s deployment with Prime Intellect compute |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Local dev, tests, code layout |

## Status

V0 control plane is **deployment-ready**. CI runs 24 tests + helm lint on every PR.
What's left to actually run on Prime Intellect is **operator work** — build & push the
worker image, create a PI custom template, expose the gateway publicly, set env, deploy.

Architecture is split control-plane / data-plane:
- **Control plane** (this repo) runs in your k8s cluster — small, CPU-only, ~$50/mo
- **GPU workers** run on PI, provisioned on demand by the autoscaler — pay only when serving

This is the same pattern Beam Cloud / Modal / RunPod use.

## License

[Apache License 2.0](LICENSE) — permissive, allows commercial use, standard for cloud platform code. If you have a strong preference for AGPL or MIT, change `LICENSE` and this section before public release.
