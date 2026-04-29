# Deploying Serverless-GPU

Three paths, increasing in real-world readiness. Pick the one that matches
where you are.

| Path | What you get | Who it's for |
|---|---|---|
| **A. Local fake** | Whole stack on your laptop, no GPU, fake worker | First-time evaluators; iterating on gateway/SDK code |
| **B. Local + real PI** | Gateway on laptop via cloudflared tunnel, real PI workers | Validating the PI integration end-to-end before committing to a cluster |
| **C. K8s + real PI** | Production-ish: gateway on a managed k8s cluster, real PI workers | Actual production |

Read whichever section applies. They're independent.

---

## Path A — Local fake (5 min, $0)

```bash
git clone <this repo>
cd Serverless-GPU
make install
make test                                 # 33 tests should pass

cp .env.example .env
make compose-up                           # gateway + redis + 1 fake worker

# new terminal
source .venv/bin/activate
serverlessgpu deploy sdk/examples/qwen.py:qwen
serverlessgpu run qwen --payload '{"prompt": "hello"}'
serverlessgpu stream qwen --payload '{"prompt": "hello"}'  # SSE token streaming
```

Expected output: a fake echo response with `"completion": "[fake response from Qwen/Qwen2.5-7B-Instruct] you sent: ..."`. **Proves the whole control-plane round-trip works** without burning GPU money. The fake worker stands in for a real PI pod.

Tear down:
```bash
docker compose down       # preserves redis volume (deployments survive)
docker compose down -v    # nukes redis volume too
```

---

## Path B — Local + real PI (15 min, ~$0.30 in PI credit)

This is what proves the **whole stack** works end-to-end with real GPU compute, before you commit to a managed cluster.

### Prerequisites

- A PI API key with at least `pods:read` and `pods:write` scope ([PI dashboard](https://app.primeintellect.ai/dashboard))
- The `serverlessgpu-pi-worker` image built + pushed somewhere PI can pull from. CI publishes to `ghcr.io/<your-fork-owner>/serverlessgpu-pi-worker:latest` on every main commit. Make the GHCR package public for unauthenticated pulls.
- A PI **custom template** referencing that image — create via PI dashboard, copy the resulting template id. PI's API has no template-create endpoint; this is a one-time UI click.
- `cloudflared` installed: `brew install cloudflared` (macOS) or [download](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) for other platforms.

### Walkthrough

**1. Verify your PI key works:**
```bash
PI_API_KEY=pit_... .venv/bin/serverlessgpu pi-check
# → "key works - 0 pod(s) on this account"
```

**2. Open a public tunnel to localhost:8080:**
```bash
cloudflared tunnel --url http://localhost:8080
# Copy the https://...trycloudflare.com URL from output
```

**3. Bring up gateway + redis with real-PI provider:**

```bash
export PI_API_KEY=pit_...
export PI_CUSTOM_TEMPLATE_ID=tmpl-xyz   # from PI dashboard

# This handles steps 2 + 3 in one command: opens the cloudflared tunnel,
# extracts the URL, brings up docker compose with all the right env wired in.
make pi-up
```

(If you'd rather run it by hand: skip `make pi-up`, do step 2 manually, then
in a second terminal `export GATEWAY_PUBLIC_URL=https://...trycloudflare.com
&& AUTOSCALER=1 PROVIDER=primeintellect docker compose up --build gateway redis`.)

The gateway will fail-fast at startup if any required env is missing — clean error, not a confusing 401 loop later.

**4. Deploy a real model and run inference:**

In a third terminal:
```bash
source .venv/bin/activate

serverlessgpu deploy sdk/examples/qwen.py:qwen

# First request triggers the autoscaler → PI provision → ~60s cold start
time serverlessgpu run qwen --payload '{"prompt": "Hello", "max_tokens": 32}'
```

You should see a real LLM response. Watch `docker compose logs gateway` to see the autoscaler tick, provision, worker register, request flow through.

**5. Watch idle scale-down:**

```bash
# After 5 minutes idle (configurable via app spec idle_timeout_s)
.venv/bin/serverlessgpu pi-check
# → "0 pod(s) on this account" — the worker terminated, you stopped paying
```

**6. Tear down:**
```bash
serverlessgpu delete qwen --yes        # removes app, drains workers
docker compose down -v
# kill cloudflared
```

### What goes wrong, in order of likelihood

| Symptom | Cause | Fix |
|---|---|---|
| Gateway exits on startup with `PROVIDER=primeintellect requires [...]` | Missing or stub-value env var | Set the listed vars, or use `replace-me` literal won't help — must be a real value |
| `pi-check` returns 401 | Key revoked or wrong | Generate a new key in PI dashboard |
| `pi-check` works but `provision` fails with "Field required cloudId" | You set `PI_CLOUD_ID=""` explicitly | Unset it (default is `runpod`) |
| Worker registers, request enqueues, but never resolves | Worker can't reach `GATEWAY_PUBLIC_URL` from inside the PI pod | Verify the tunnel URL works from outside your network: `curl https://...trycloudflare.com/health` |
| `provision` succeeds but pod stays PROVISIONING forever | PI is out of stock for that GPU type at this provider/cloudId | Try a different `gpu` (RTX 3090 has the highest stock), or `--set primeintellect.providerType=fluidstack` |
| Request handled but takes ~5 min for first response | First-time HuggingFace model download in the pod | Subsequent requests will be ~3s; first request includes model weight download |

---

## Path C — K8s + real PI (production-ish, ~$50-100/mo cluster cost + per-request PI cost)

Same architecture as Path B but the gateway runs on a real cluster behind a real ingress with real TLS. Workers still run on PI.

### Prerequisites

- A k8s cluster with:
  - An ingress controller (default in this chart: nginx-ingress)
  - cert-manager + a `letsencrypt-prod` ClusterIssuer (or override `ingress.tls.issuer`)
  - A default `StorageClass` for the Redis PVC
  - **Cheapest options**: DigitalOcean DOKS (~$12/mo control plane), Linode LKE, GKE Autopilot
- Same PI prerequisites as Path B (key, template id, public worker image)
- Helm 3 installed locally
- DNS pointed at your ingress controller's external IP

### Walkthrough

**1. Copy the values template:**
```bash
cp deploy/helm/serverlessgpu/values-prod.example.yaml my-values.yaml
# Edit my-values.yaml — every field has guidance comments
```

**2. Generate gateway API keys:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Add to my-values.yaml under gateway.apiKeys
```

**3. Sanity-check the values file (catches leftover TODO_ placeholders):**
```bash
make verify-values VALUES=my-values.yaml
```

**4. Install:**
```bash
helm install sgpu deploy/helm/serverlessgpu/ \
  --namespace sgpu --create-namespace \
  -f my-values.yaml \
  --wait --timeout 5m
```

**5. Verify pods + DNS + TLS:**
```bash
kubectl -n sgpu get pods
kubectl -n sgpu rollout status deployment/sgpu-serverlessgpu-gateway

curl https://api.your-domain.com/health
# → {"ok":true}
curl https://api.your-domain.com/ready
# → {"ok":true,"redis":"ok"}
```

**6. Deploy + run:**
```bash
export SERVERLESSGPU_GATEWAY=https://api.your-domain.com
export SERVERLESSGPU_API_KEY=<one of your generated keys>

serverlessgpu deploy sdk/examples/qwen.py:qwen
serverlessgpu run qwen --payload '{"prompt": "hello"}'
```

### Production checklist

- [ ] Multiple `gateway.apiKeys` for zero-downtime key rotation
- [ ] `gateway.replicaCount: 2+` (chart already does anti-affinity)
- [ ] `redis.storage.size`: bump to 5-10Gi for real workloads
- [ ] Wire `/metrics` into Prometheus — see [OPERATIONS.md](OPERATIONS.md#observability)
- [ ] Import the [Grafana dashboard](../deploy/grafana/serverlessgpu.json)
- [ ] Set `primeintellect.maxPriceHr` as a runaway-cost bound
- [ ] Test the full path including idle scale-down before sending real traffic

### Upgrades

```bash
# CI publishes :latest on every main commit
helm upgrade sgpu deploy/helm/serverlessgpu/ \
  --namespace sgpu \
  -f my-values.yaml \
  --reuse-values \
  --set image.tag=<new-sha>
```

For zero-downtime: gateway is stateless; rolling update happens automatically. Redis StatefulSet rolls one pod at a time.

### Tear down

```bash
helm uninstall sgpu -n sgpu
kubectl delete namespace sgpu                # also deletes the PVC
```

---

## Cost expectations

| Path | Monthly cost | Per-request cost |
|---|---|---|
| A (local fake) | $0 | $0 |
| B (local + PI) | $0 (control plane) | ~$0.40/hr/H100 while a worker is alive (idle scale-down stops the meter) |
| C (k8s + PI) | $50-100 (small managed cluster) | Same as B for GPU |

Typical app: idle most of the time → near-$0; under load → pays per GPU-second of actual inference, no idle charge once `idle_timeout_s` elapses.
