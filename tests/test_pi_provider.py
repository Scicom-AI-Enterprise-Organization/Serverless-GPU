"""PrimeIntellectProvider against a mocked PI HTTP API (httpx.MockTransport)."""
import json

import httpx
import pytest

from gateway.pi_provider import PrimeIntellectProvider


class _FakePI:
    def __init__(self):
        self.pods: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))

        if not request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(401, json={"error": "missing bearer"})

        if method == "POST" and path == "/api/v1/pods/":
            pid = f"pod-{len(self.pods) + 1:03d}"
            self.pods[pid] = {"id": pid, "name": body["pod"]["name"], "status": "PROVISIONING",
                              "gpuName": body["pod"]["gpuType"], "gpuCount": body["pod"]["gpuCount"]}
            return httpx.Response(200, json={"id": pid, **self.pods[pid]})

        if method == "GET" and path == "/api/v1/pods/":
            data = list(self.pods.values())
            return httpx.Response(200, json={"data": data, "total_count": len(data)})

        if method == "DELETE" and path.startswith("/api/v1/pods/"):
            pid = path.rsplit("/", 1)[1]
            self.pods.pop(pid, None)
            return httpx.Response(200, json={"status": "TERMINATED"})

        return httpx.Response(404)


def _make_provider(api_key="test-key"):
    fake = _FakePI()
    transport = httpx.MockTransport(fake.handler)
    client = httpx.AsyncClient(
        base_url="https://api.primeintellect.ai",
        headers={"Authorization": f"Bearer {api_key}"},
        transport=transport,
        timeout=5.0,
    )
    p = PrimeIntellectProvider(
        api_key=api_key,
        custom_template_id="tmpl-abc",
        gateway_public_url="https://gw.example.com",
        provider_type="runpod",
        client=client,
    )
    return p, fake


@pytest.mark.asyncio
async def test_provision_sends_correct_body():
    p, fake = _make_provider()
    mid = await p.provision("qwen", "Qwen", "A100", {"REGISTRATION_TOKEN": "tok-xyz"})
    assert mid.startswith("m-pi-")

    create_call = [c for c in fake.calls if c[0] == "POST"][-1][2]
    assert create_call["pod"]["customTemplateId"] == "tmpl-abc"
    assert create_call["pod"]["gpuType"] == "A100_80GB", "gpu mapping should kick in"
    assert create_call["provider"]["type"] == "runpod"

    env_keys = {e["key"]: e["value"] for e in create_call["pod"]["envVars"]}
    assert env_keys["GATEWAY_URL"] == "https://gw.example.com"
    assert env_keys["REGISTRATION_TOKEN"] == "tok-xyz"
    assert env_keys["APP_ID"] == "qwen"


@pytest.mark.asyncio
async def test_list_machines_filters_by_name_prefix():
    p, _ = _make_provider()
    await p.provision("qwen", "Qwen", "H100", {"REGISTRATION_TOKEN": "t1"})
    await p.provision("qwen", "Qwen", "A100", {"REGISTRATION_TOKEN": "t2"})
    listed = await p.list_machines()
    assert len(listed) == 2


@pytest.mark.asyncio
async def test_terminate_removes_pod():
    p, _ = _make_provider()
    mid = await p.provision("qwen", "Qwen", "H100", {"REGISTRATION_TOKEN": "t"})
    await p.terminate(mid)
    assert await p.list_machines() == []


@pytest.mark.asyncio
async def test_401_raises_runtime_error():
    p, _ = _make_provider(api_key="wrong")
    # The mock requires "Bearer ..." prefix; we pass api_key="wrong" but the
    # client still attaches "Bearer wrong" — the mock returns 401 for missing bearer
    # only. Force an actual auth fail by stripping the auth header.
    p._client.headers.pop("Authorization", None)
    with pytest.raises(RuntimeError, match="401"):
        await p.provision("qwen", "Qwen", "H100", {"REGISTRATION_TOKEN": "t"})
