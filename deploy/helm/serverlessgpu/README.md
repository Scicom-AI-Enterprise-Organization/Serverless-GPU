# Helm chart: serverlessgpu

> See [`overview.md`](overview.md) for what this chart actually deploys —
> components, k8s objects with names, ASCII architecture diagram.

Deploys the **control plane** to a k8s cluster:
- gateway (Deployment, ≥2 replicas, behind a Service + Ingress)
- redis (StatefulSet with PVC for AOF persistence)

GPU workers run **externally** on Prime Intellect — provisioned by the
gateway's autoscaler. This chart does not put GPU pods inside your cluster.

## Images

Built + pushed by CI on every push to `main`. Two images live on GHCR:

| Image | Where it runs | Pull from |
|---|---|---|
| `ghcr.io/<owner>/serverlessgpu-gateway` | this k8s cluster | the cluster |
| `ghcr.io/<owner>/serverlessgpu-pi-worker` | Prime Intellect pods | PI |

Tags: `<short-commit-sha>` (immutable) and `latest` (rolling). For production
deploys, pin to a SHA — `latest` is for dev convenience only.

If you've forked the repo, set `image.registry` and `workerImage.registry` to
your fork's GHCR owner namespace. If you keep the package private, also set
`image.imagePullSecrets`.

## Quick start

```bash
# Replace `yourorg` with your GitHub username/org (the GHCR namespace CI pushes to).
helm install sgpu deploy/helm/serverlessgpu \
  --namespace serverlessgpu --create-namespace \
  --set image.registry=ghcr.io/yourorg \
  --set image.tag=latest \
  --set workerImage.registry=ghcr.io/yourorg \
  --set ingress.host=api.your-domain.com \
  --set gateway.publicUrl=https://api.your-domain.com \
  --set gateway.provider=primeintellect \
  --set primeintellect.apiKey=$PI_API_KEY \
  --set primeintellect.customTemplateId=$PI_CUSTOM_TEMPLATE_ID
```

The `customTemplateId` should reference the `serverlessgpu-pi-worker` image
in PI — create the custom template in the PI dashboard pointing at
`ghcr.io/yourorg/serverlessgpu-pi-worker:latest`.

## What it assumes about the cluster

- An ingress controller (default: nginx-ingress; change `ingress.className`)
- cert-manager with a `letsencrypt-prod` ClusterIssuer (or pass your own)
- A default `StorageClass` for the Redis PVC

## Staging without burning GPU $$

Set `gateway.provider=fake` to use the in-process FakeProvider — gateway
will "spawn" workers as asyncio tasks rather than calling PI. Lets you
exercise the full control-plane path against a real ingress + real Redis.

## What's deliberately not in here

- Postgres for app-definition durability (Redis AOF + PVC is good enough for V0)
- HPA on the gateway (start with replicaCount: 2, scale by hand)
- Multi-region (single-region V0)
- Worker pods (they live on PI, not in this cluster)
