# Contributing

## Getting set up

```bash
make install              # uv venv at .venv/, install all 3 packages editable + pytest
source .venv/bin/activate
make test                 # 24 tests, ~0.5s
```

That's it. No services to run for the test suite — fakeredis is in-memory.

## Code layout

```
gateway/                  FastAPI gateway
  gateway/                  package
    main.py                   routes + middleware + lifespan
    auth.py                   bearer-token validator
    metrics.py                Prometheus registry + scrape rendering
    provider.py               Provider ABC + FakeProvider
    pi_provider.py            PrimeIntellectProvider
    autoscaler.py             1Hz scale loop
    reconciler.py             5s GC loop
  pyproject.toml
  Dockerfile

worker-agent/             worker that BRPOPs jobs and runs the model
  worker_agent/main.py      register → poll loop → handle (fake or vllm)
  pyproject.toml
  Dockerfile                local-dev image (fake mode)
  Dockerfile.pi             PI custom-template image (vllm + worker-agent)
  entrypoint-pi.sh

sdk/                      Python SDK + CLI
  serverlessgpu/
    decorators.py             @endpoint, QueueDepthAutoscaler
    cli.py                    deploy/run/stream/list/show/delete/pi-check
  examples/qwen.py
  pyproject.toml

tests/                    pytest suite (in-memory, 24 tests)
  conftest.py               shared fixtures (gateway_url, fake redis)
  test_*.py                 one file per concern

deploy/
  helm/serverlessgpu/       k8s chart
  grafana/serverlessgpu.json  8-panel dashboard

docker-compose.yml        local stack (redis + gateway + 1 fake worker)
Makefile                  install / test / compose-* / helm-*
```

## Workflow

Iteration loop:
1. Edit code (gateway or worker). The packages are installed editable; no rebuild needed.
2. `make test` — fast (~0.5s).
3. `make compose-up` to exercise locally with a real Redis.
4. Push → CI runs `make test` on Python 3.11 + 3.12 plus `helm lint`.

Adding a new test:
- Drop a `tests/test_<thing>.py` file
- Use the `gateway_url` (no autoscaler) or `gateway_url_with_autoscaler` fixture
- For Redis introspection inside the test, use the `fake_redis_server` fixture and create a fresh client

Adding a new provider:
- Subclass `gateway.provider.Provider` (4 methods)
- Wire it into `gateway.provider.build_provider`
- Add a test under `tests/test_<provider>_provider.py` using `httpx.MockTransport` (see `tests/test_pi_provider.py` for the template)

Adding a new gateway route:
- Decorate with `@app.<method>("/path", dependencies=[Depends(require_api_key)])` if it's a user route
- Add a test in `tests/test_lifecycle.py` (or a new file)
- Update the routes table in `docs/ARCHITECTURE.md`

## Running individual tests

```bash
.venv/bin/python -m pytest tests/test_streaming.py -v
.venv/bin/python -m pytest tests/test_lifecycle.py::test_admission_429_when_queue_at_cap -v
.venv/bin/python -m pytest tests/ -k "auth"
```

## Style

- Python ≥ 3.10 (we use `set[str]`, `list[str]`, `|` union syntax)
- No `from typing import Set, List` — use built-ins
- Single quotes for short strings, double for docstrings (loose convention, not enforced yet)
- No black/ruff config yet — feel free to land one

## Things we'd love help with

- Real-Redis integration tests in pytest (currently only fakeredis-based suites are in CI; the throwaway `test_real_redis.py` got removed but the scenarios are worth porting properly)
- Streaming + cancellation tests under pytest (event loop ownership is fiddly)
- Postgres backend for app records (when we need durability beyond Redis AOF)
- A second provider (RunPod / Lambda Labs / generic SSH)
- Grafana dashboard refinements
- A "cookbook" of `@endpoint` examples beyond Qwen
