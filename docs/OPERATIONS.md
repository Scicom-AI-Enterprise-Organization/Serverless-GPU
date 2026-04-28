# Operations reference

Operational details that don't fit in the README quickstart. For deploy
walkthroughs see [DEPLOY.md](DEPLOY.md). For architecture see
[ARCHITECTURE.md](ARCHITECTURE.md).

## Auth

Set `GATEWAY_API_KEYS` to a comma-separated list of bearer tokens. All
user-facing routes (`/apps`, `/run`, `/stream`, `/result`) require
`Authorization: Bearer <key>`. `/health`, `/ready`, `/metrics` are always
open. `/workers/*` use the existing one-shot registration token flow.

Generate a key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Local dev:
```bash
export GATEWAY_API_KEYS="dev-key-1,dev-key-2"
export SERVERLESSGPU_API_KEY="dev-key-1"   # the CLI auto-attaches this
```

Helm:
```bash
helm install ... --set gateway.apiKeys="key1\,key2"
```

When `GATEWAY_API_KEYS` is empty (default), auth is **disabled** — fine for
local dev, **must be set before exposing the ingress publicly**.

Multi-key supports zero-downtime rotation: add a new key to the list,
switch clients to it, then drop the old key.

## Health probes

| Endpoint | Purpose | What it checks |
|---|---|---|
| `GET /health` | k8s **liveness** | Process is alive. **Does NOT check Redis** — restarting the gateway can't fix Redis problems, and we don't want crashloops. Always 200. |
| `GET /ready` | k8s **readiness** | Pings Redis. Returns 503 if unreachable so k8s removes the pod from the Service's endpoints (depools traffic). |
| `GET /metrics` | Prometheus | Auth-exempt. See Observability below. |

## Per-request timeouts

Each app spec carries `request_timeout_s` (default 600). The gateway stamps
this into every job; the worker wraps `handle()` (or each chunk in stream
mode) in `asyncio.wait_for`. On timeout the worker writes `status=timeout`
(unary) or publishes a `{"timeout": true, "done": true}` chunk (streaming)
and moves on. Stuck vLLM no longer wedges the worker — it releases capacity
back to the queue.

Override per app at deploy time:
```python
@endpoint(model="...", gpu="H100", request_timeout_s=300)
def my_app(): pass
```

## Observability

`GET /metrics` exposes Prometheus-format metrics:

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `gateway_requests_total` | counter | `route`, `status` | every HTTP request, with route template (e.g. `/run/{app_id}`) so cardinality is bounded |
| `gateway_inflight_requests` | gauge | — | requests currently being handled |
| `gateway_queue_length` | gauge | `app_id` | sampled per-scrape from Redis LLEN |
| `gateway_workers` | gauge | `app_id` | live workers per app (sampled per-scrape) |
| `gateway_provision_total` | counter | `provider`, `ok` | scale-up attempts via Provider.provision |
| `gateway_terminate_total` | counter | `provider`, `ok` | scale-down attempts via Provider.terminate |

Grafana dashboard at [`deploy/grafana/serverlessgpu.json`](../deploy/grafana/serverlessgpu.json) — 8
panels with templated `app_id` filter.

Wire into Prometheus (without ServiceMonitor — a manual scrape config):
```yaml
scrape_configs:
  - job_name: serverlessgpu-gateway
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_component]
        action: keep
        regex: gateway
```

If you run kube-prometheus-stack or another operator that watches
`ServiceMonitor` CRs, add one manually pointing at the
`{release}-serverlessgpu-gateway` Service on the `http` port, path `/metrics`.

## Sanity-check commands

```bash
# pytest
make test

# helm chart still renders cleanly
make helm-template

# what's the gateway doing right now
docker compose logs gateway --tail 100 -f
docker compose logs worker-fake --tail 100 -f

# what's in Redis
docker compose exec redis redis-cli
> KEYS *
> LLEN queue:qwen
> SCARD worker_index:qwen

# real-PI account state
PI_API_KEY=... .venv/bin/serverlessgpu pi-check

# k8s pods + logs
kubectl -n sgpu get pods
kubectl -n sgpu logs -l app.kubernetes.io/component=gateway --tail=100 -f
```

## Tear-down cheat sheet

```bash
# Local docker-compose
docker compose down            # preserves redis volume
docker compose down -v         # nukes redis volume too

# K8s
helm uninstall sgpu -n sgpu
kubectl delete namespace sgpu  # also deletes the PVC

# Active PI pods (defensive — autoscaler should have torn them down)
PI_API_KEY=... .venv/bin/serverlessgpu pi-check
# if any are listed:
curl -X DELETE -H "Authorization: Bearer $PI_API_KEY" \
  "https://api.primeintellect.ai/api/v1/pods/<pod-id>"
```
