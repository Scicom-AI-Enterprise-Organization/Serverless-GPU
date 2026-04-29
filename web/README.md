# web

Console for the serverless-gpu inference gateway. Multi-tenant Runpod-style
UI for deploying and monitoring vLLM serverless endpoints.

## Stack

- Next.js 16 (App Router) + React 19
- Tailwind CSS v4 + Radix UI primitives
- next-themes for light / dark
- Sonner for toasts
- Talks to the FastAPI gateway over `NEXT_PUBLIC_GATEWAY_URL`

## Quickstart

```bash
cd web
cp .env.example .env.local           # point NEXT_PUBLIC_GATEWAY_URL at your gateway
npm install
npm run dev
```

Open <http://localhost:3000>.

## Environment

| Var | Purpose |
| --- | --- |
| `NEXT_PUBLIC_GATEWAY_URL` | Public-facing gateway base URL (e.g. `https://serverlessgpu.aies.scicom.dev`). Snippets render this. |
| `RUNPOD_API_KEY` | Server-side. Used by `/api/runpod/pods` to read pod state for the Workers tab. Pull from `serverlessgpu-runpod` secret. |
| `RUNPOD_NAME_PREFIX` | Pod name prefix the gateway uses (default `serverlessgpu`). Filter for which pods belong to this UI's gateway. |
| `SERVERLESSGPU_NAMESPACE` | k8s namespace for `kubectl exec` paths in `/api/cluster/*` (default `serverlessgpu`). |

The gateway's session token (set by `/api/auth/login`) lives in an httpOnly
`sgpu_token` cookie. There is no separate `GATEWAY_API_KEY` — every request
the proxy makes inherits the signed-in user's bearer token.

## Routes

| Route | What |
| --- | --- |
| `/` | Public landing page with subway-surfers backdrop |
| `/login`, `/register` | Auth (talks to `/auth/login`, `/auth/register` on the gateway) |
| `/serverless` | Endpoint list, owner-scoped |
| `/serverless/new` | Worker Hub catalog → deploy modal (vLLM only today) |
| `/serverless/[id]` | Detail — Overview · Playground · Queue · Workers |
| `/api-keys` | Reveal / copy your bearer token |
| `/api/auth/{login,register,logout,me,token}` | Auth surface; manages the `sgpu_token` cookie |
| `/api/proxy/[...]` | Forwards browser → gateway with the cookie attached |
| `/api/runpod/pods` | Server-side fetch of RunPod pod state (Workers tab) |
| `/api/cluster/queue` | Reads queue + recent results out of Redis via `kubectl exec` |
| `/api/cluster/worker-logs` | Tails gateway logs filtered to a `machine_id` |

## Notes / known limits

- **Container logs aren't streamed** — RunPod's REST and GraphQL APIs don't
  expose pod logs. The Workers tab shows gateway-side lifecycle events
  (`provision / register / scale / terminate`). Container stdout would need
  the worker container to ship lines into Redis itself.
- **Per-app request history** isn't on the gateway today; the Queue tab
  works by scanning Redis directly. The Playground tab tracks UI-fired
  requests in `localStorage` per browser.
- **Cluster-side proxies** (`/api/cluster/*`) shell out to `kubectl exec` on
  the gateway pod's Redis. Dev-mode shape — needs a valid kubectl credential
  (Teleport: `tsh kube login <cluster>`).
- **Drop a Subway Surfers clip at `public/videos/subway.mp4`** to enable the
  homepage background video — without it the page falls back to the
  gradient + scicom-logo poster.

## Production build

```bash
npm run build
npm start
```

## License

Same as the parent `serverless-gpu` repo.
