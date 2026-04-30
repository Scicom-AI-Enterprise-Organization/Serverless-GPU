"""Prime Intellect provider — calls the PI HTTP API to launch GPU pods.

Requires (env vars):
  PI_API_KEY              - bearer token from the PI dashboard
  PI_CUSTOM_TEMPLATE_ID   - id of a pre-baked PI template containing our
                            worker-agent (+ vllm). Templates are how PI lets
                            you bring your own image — there's no `userData`
                            field on pod-create, only env-var injection.
  GATEWAY_PUBLIC_URL      - the URL workers dial back to from PI pods (must
                            be reachable from PI's network)

Optional:
  PI_API_BASE             - default: https://api.primeintellect.ai
  PI_PROVIDER_TYPE        - default: runpod (PI re-sells from runpod, fluidstack,
                            lambdalabs, etc. — see PI dashboard for cloudIds)
  PI_CLOUD_ID             - default: "runpod" (validated working in smoke test).
                            PI rejects empty cloudId with "Field required".
  PI_GPU_SOCKET           - default: PCIe
  PI_DATA_CENTER_ID       - optional, pin a region
  PI_MAX_PRICE_HR         - optional, ceiling price per GPU-hour
  PI_NAME_PREFIX          - default: serverlessgpu — pods we own get this prefix
                            so list_machines() can filter them out from any
                            other pods on the same account.

Caveats:
  - Pods take ~30-90s to reach ACTIVE state. provision() returns the machine_id
    immediately; the worker registers asynchronously when its container starts.
  - GPU type strings: our app spec uses "H100"; PI uses "H100_80GB". A small
    mapping table here translates.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import httpx

from .provider import Provider

logger = logging.getLogger("gateway.pi_provider")


_DEFAULT_INFERENCE_IMAGE = "public.ecr.aws/o6x1g6b0/serverlessgpu-inference-agent:latest"


def _render_bootstrap_script(
    *,
    model_id: str,
    hf_repo: str,
    gateway_url: str,
    worker_token: str,
    image: Optional[str] = None,
) -> str:
    """Bash run on the PI host (via SSH) to docker-run the inference agent.

    The PI `ubuntu_22_cuda_12` base image already has Docker + NVIDIA runtime,
    so we just `docker pull` our agent and `docker run`.
    """
    img = image or os.environ.get("PI_INFERENCE_IMAGE", _DEFAULT_INFERENCE_IMAGE)
    # Single-quoted heredoc keeps $VAR refs verbatim — agent reads them at runtime.
    # We escape $ inside the heredoc only where we want it expanded *now*.
    return f"""#!/usr/bin/env bash
set -euo pipefail
exec >>/var/log/sgpu-bootstrap.log 2>&1
echo "=== sgpu bootstrap $(date) ==="

# HF model cache survives container restarts on the same host.
mkdir -p /root/cache

# Pre-fetch the public agent image (no AWS auth needed for ECR Public).
docker pull {img}

# Tear down any prior agent on this host (idempotent re-run).
docker rm -f sgpu-agent 2>/dev/null || true

docker run -d \\
    --name sgpu-agent \\
    --gpus all \\
    --restart unless-stopped \\
    -p 8000:8000 \\
    -v /root/cache:/root/.cache/huggingface \\
    -e MODEL_ID={model_id!r} \\
    -e MODEL_REPO={hf_repo!r} \\
    -e GATEWAY_URL={gateway_url!r} \\
    -e WORKER_TOKEN={worker_token!r} \\
    -e PORT=8000 \\
    {img}

echo "agent container launched"
"""


_GPU_NAME_MAP = {
    "H100": "H100_80GB",
    "A100": "A100_80GB",
    "A100-40G": "A100_40GB",
    "A10G": "A10",
    "A10": "A10",
    "L40S": "L40S",
    "L4": "L4",
    "RTX4090": "RTX4090_24GB",
    "RTX3090": "RTX3090_24GB",
}


def _map_gpu(name: str) -> str:
    """Normalize app-spec GPU strings to PI's gpuName enum."""
    if name in _GPU_NAME_MAP:
        return _GPU_NAME_MAP[name]
    return name  # assume caller already used PI's enum (e.g. "H100_80GB")


class PrimeIntellectProvider(Provider):
    name = "primeintellect"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        custom_template_id: Optional[str] = None,
        gateway_public_url: Optional[str] = None,
        provider_type: Optional[str] = None,
        cloud_id: Optional[str] = None,
        gpu_socket: Optional[str] = None,
        data_center_id: Optional[str] = None,
        max_price_hr: Optional[float] = None,
        name_prefix: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("PI_API_KEY")
        if not self.api_key:
            raise RuntimeError("PI_API_KEY env var or constructor arg required")

        self.api_base = (api_base or os.environ.get("PI_API_BASE", "https://api.primeintellect.ai")).rstrip("/")
        self.custom_template_id = custom_template_id or os.environ.get("PI_CUSTOM_TEMPLATE_ID")
        if not self.custom_template_id:
            raise RuntimeError(
                "PI_CUSTOM_TEMPLATE_ID env var required — bake a template "
                "containing our worker image and set its id here"
            )

        self.gateway_public_url = (
            gateway_public_url
            or os.environ.get("GATEWAY_PUBLIC_URL")
            or os.environ.get("GATEWAY_URL")
        )
        if not self.gateway_public_url:
            raise RuntimeError(
                "GATEWAY_PUBLIC_URL env var required so PI workers can reach the gateway"
            )

        self.provider_type = provider_type or os.environ.get("PI_PROVIDER_TYPE", "runpod")
        # PI rejects empty cloudId. "runpod" verified working against the live
        # API in our smoke test; override for other providers (fluidstack, etc).
        self.cloud_id = cloud_id or os.environ.get("PI_CLOUD_ID", "runpod")
        self.gpu_socket = gpu_socket or os.environ.get("PI_GPU_SOCKET", "PCIe")
        self.data_center_id = data_center_id or os.environ.get("PI_DATA_CENTER_ID")
        self.max_price_hr = max_price_hr if max_price_hr is not None else (
            float(os.environ.get("PI_MAX_PRICE_HR")) if os.environ.get("PI_MAX_PRICE_HR") else None
        )
        self.name_prefix = name_prefix or os.environ.get("PI_NAME_PREFIX", "serverlessgpu")

        self._client = client or httpx.AsyncClient(
            base_url=self.api_base,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30.0,
        )
        self._owns_client = client is None

        # local map machine_id (our id baked into pod name) → pod_id (PI's id).
        self._pod_ids: dict[str, str] = {}

    async def provision(self, app_id: str, model: str, gpu: str, env: dict[str, str]) -> str:
        import uuid as _uuid

        machine_id = f"m-pi-{_uuid.uuid4().hex[:8]}"
        pod_name = f"{self.name_prefix}-{app_id}-{machine_id}"

        env_vars = [
            {"key": "APP_ID", "value": app_id},
            {"key": "MACHINE_ID", "value": machine_id},
            {"key": "MODEL_ID", "value": model},
            {"key": "GATEWAY_URL", "value": self.gateway_public_url},
            {"key": "REGISTRATION_TOKEN", "value": env.get("REGISTRATION_TOKEN", "")},
            {"key": "WORKER_MODE", "value": env.get("WORKER_MODE", "vllm")},
        ]
        for k, v in env.items():
            if k in {"APP_ID", "MACHINE_ID", "MODEL_ID", "GATEWAY_URL", "REGISTRATION_TOKEN", "WORKER_MODE"}:
                continue
            env_vars.append({"key": k, "value": v})

        body: dict[str, Any] = {
            "pod": {
                "name": pod_name,
                "cloudId": self.cloud_id,
                "gpuType": _map_gpu(gpu),
                "socket": self.gpu_socket,
                "gpuCount": 1,
                "customTemplateId": self.custom_template_id,
                "envVars": env_vars,
                "autoRestart": False,
            },
            "provider": {"type": self.provider_type},
        }
        if self.data_center_id:
            body["pod"]["dataCenterId"] = self.data_center_id
        if self.max_price_hr is not None:
            body["pod"]["maxPrice"] = self.max_price_hr

        r = await self._client.post("/api/v1/pods/", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"PI provision failed: {r.status_code} {r.text}")
        data = r.json()
        pod_id = data.get("id") or data.get("data", {}).get("id")
        if not pod_id:
            raise RuntimeError(f"PI provision response missing id: {data}")

        self._pod_ids[machine_id] = pod_id
        logger.info(
            "pi-provision: app=%s gpu=%s → machine=%s pod=%s name=%s",
            app_id, gpu, machine_id, pod_id, pod_name,
        )
        return machine_id

    async def terminate(self, machine_id: str) -> None:
        pod_id = self._pod_ids.pop(machine_id, None)
        if pod_id is None:
            # Fall back to lookup by name prefix in case we restarted and lost
            # the in-memory map. List then match.
            pod_id = await self._lookup_pod_id_by_machine_id(machine_id)
            if pod_id is None:
                logger.warning("pi-terminate: no pod_id known for machine %s", machine_id)
                return

        r = await self._client.delete(f"/api/v1/pods/{pod_id}")
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"PI terminate failed: {r.status_code} {r.text}")
        logger.info("pi-terminate: %s (pod=%s) torn down", machine_id, pod_id)

    async def list_machines(self) -> list[str]:
        """List active pods owned by this gateway (filtered by name prefix)."""
        out: list[str] = []
        offset = 0
        while True:
            r = await self._client.get(
                "/api/v1/pods/", params={"offset": offset, "limit": 100}
            )
            if r.status_code >= 400:
                raise RuntimeError(f"PI list_pods failed: {r.status_code} {r.text}")
            page = r.json()
            data = page.get("data", [])
            for pod in data:
                name = pod.get("name", "")
                if not name.startswith(f"{self.name_prefix}-"):
                    continue
                # Pull machine_id back out of the name suffix
                # name format: {prefix}-{app_id}-{machine_id}
                parts = name.rsplit("-", 2)
                if len(parts) < 3:
                    continue
                machine_id = "-".join(parts[-2:]) if parts[-2].startswith("m") else parts[-1]
                # The simple parse: find "m-pi-XXXX" inside the name
                idx = name.find("m-pi-")
                if idx >= 0:
                    machine_id = name[idx:]
                out.append(machine_id)
                self._pod_ids.setdefault(machine_id, pod["id"])
            if len(data) < 100:
                break
            offset += 100
        return out

    async def get_pod_status(self, machine_id: str) -> Optional[dict[str, Any]]:
        """Useful for diagnostics: fetch the full pod record."""
        pod_id = self._pod_ids.get(machine_id) or await self._lookup_pod_id_by_machine_id(machine_id)
        if pod_id is None:
            return None
        r = await self._client.get(f"/api/v1/pods/{pod_id}")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"PI get_pod failed: {r.status_code} {r.text}")
        return r.json()

    async def _lookup_pod_id_by_machine_id(self, machine_id: str) -> Optional[str]:
        offset = 0
        while True:
            r = await self._client.get(
                "/api/v1/pods/", params={"offset": offset, "limit": 100}
            )
            if r.status_code >= 400:
                return None
            page = r.json()
            data = page.get("data", [])
            for pod in data:
                if machine_id in (pod.get("name", "") or ""):
                    return pod["id"]
            if len(data) < 100:
                break
            offset += 100
        return None

    # ------------------------------------------------------------------
    # Inference-only extensions: network volumes + vLLM-image pods.
    # ------------------------------------------------------------------

    async def create_network_volume(self, name: str, size_gb: int = 50) -> str:
        """Create a per-endpoint persistent volume for the HF/vLLM cache.

        Returns the PI volume id. Best-effort — on PI variants without
        volume support this will raise; caller can fall back to ephemeral.
        """
        body = {
            "name": name,
            "size": size_gb,
            "cloudId": self.cloud_id,
        }
        if self.data_center_id:
            body["dataCenterId"] = self.data_center_id
        r = await self._client.post("/api/v1/network-volumes/", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"PI create_network_volume failed: {r.status_code} {r.text}")
        data = r.json()
        vid = data.get("id") or data.get("data", {}).get("id")
        if not vid:
            raise RuntimeError(f"PI create_network_volume response missing id: {data}")
        logger.info("pi-volume: created %s (size=%dGB) → %s", name, size_gb, vid)
        return vid

    async def delete_network_volume(self, volume_id: str) -> None:
        r = await self._client.delete(f"/api/v1/network-volumes/{volume_id}")
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"PI delete_network_volume failed: {r.status_code} {r.text}")
        logger.info("pi-volume: deleted %s", volume_id)

    async def provision_vllm(
        self,
        *,
        model_id: str,
        hf_repo: str,
        gpu: str,
        network_volume_id: Optional[str],
        worker_token: str,
        image: Optional[str] = None,  # kept for API compat; ignored
    ) -> str:
        """Spawn a raw Ubuntu+CUDA PI pod and bootstrap vLLM via SSH.

        PI's pod-create API doesn't accept a startup script, so we:
          1. Create a pod with image=ubuntu_22_cuda_12 + our SSH key
          2. Background-task: wait for SSH reachability, scp+run a bash
             bootstrap script that pip-installs vLLM, runs `vllm serve`,
             and curls /inference/internal/ready-checkin.

        Returns immediately with the machine_id; ready-checkin lands later.
        """
        import uuid as _uuid

        ssh_key_id = os.environ.get("PI_SSH_KEY_ID")
        if not ssh_key_id:
            raise RuntimeError(
                "PI_SSH_KEY_ID env var required — upload an SSH key to "
                "Prime Intellect dashboard and set its id here"
            )

        machine_id = f"m-vllm-{_uuid.uuid4().hex[:8]}"
        pod_name = f"{self.name_prefix}-vllm-{model_id[:8]}-{machine_id}"

        body: dict[str, Any] = {
            "pod": {
                "name": pod_name,
                "cloudId": self.cloud_id,
                "gpuType": _map_gpu(gpu),
                "socket": self.gpu_socket,
                "gpuCount": 1,
                "image": "ubuntu_22_cuda_12",
                "sshKeyId": ssh_key_id,
                "autoRestart": False,
                "diskSize": int(os.environ.get("PI_VLLM_DISK_GB", "100")),
            },
            "provider": {"type": self.provider_type},
        }
        if self.data_center_id:
            body["pod"]["dataCenterId"] = self.data_center_id
        if self.max_price_hr is not None:
            body["pod"]["maxPrice"] = self.max_price_hr

        r = await self._client.post("/api/v1/pods/", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"PI provision_vllm failed: {r.status_code} {r.text}")
        data = r.json()
        pod_id = data.get("id") or data.get("data", {}).get("id")
        if not pod_id:
            raise RuntimeError(f"PI provision_vllm response missing id: {data}")

        self._pod_ids[machine_id] = pod_id
        logger.info(
            "pi-provision-vllm: model=%s repo=%s gpu=%s → machine=%s pod=%s",
            model_id, hf_repo, gpu, machine_id, pod_id,
        )

        # Fire-and-forget the SSH bootstrap. The reconciler will mark the
        # cold-start as errored if ready-checkin doesn't land in time.
        asyncio.create_task(
            self._bootstrap_vllm(
                pod_id=pod_id,
                model_id=model_id,
                hf_repo=hf_repo,
                worker_token=worker_token,
            )
        )
        return machine_id

    async def _bootstrap_vllm(
        self,
        *,
        pod_id: str,
        model_id: str,
        hf_repo: str,
        worker_token: str,
    ) -> None:
        """Background: SSH into the pod once it's up and run the vLLM bootstrap."""
        try:
            import asyncssh
        except ImportError:
            logger.error("asyncssh not installed — `pip install asyncssh`")
            return

        ssh_key_path = os.environ.get("PI_SSH_PRIVATE_KEY_PATH", "/etc/sgpu/pi_id_ed25519")
        ssh_user = os.environ.get("PI_SSH_USER", "root")

        # Wait for the pod to be RUNNING and SSH info to be populated.
        host: Optional[str] = None
        port: int = 22
        deadline = time.time() + 600  # up to 10 min for hardware to come up
        while time.time() < deadline:
            try:
                rr = await self._client.get(f"/api/v1/pods/{pod_id}")
                if rr.status_code == 200:
                    pod = rr.json()
                    status = (pod.get("status") or "").upper()
                    # PI surfaces SSH details under different keys depending
                    # on the underlying provider — try a few.
                    host = (
                        pod.get("sshHost")
                        or pod.get("ip")
                        or (pod.get("sshConnection") or {}).get("host")
                    )
                    port = int(
                        pod.get("sshPort")
                        or (pod.get("sshConnection") or {}).get("port")
                        or 22
                    )
                    if status in ("RUNNING", "ACTIVE") and host:
                        break
            except Exception:
                logger.exception("ssh-bootstrap: pod status poll failed")
            await asyncio.sleep(8)

        if not host:
            logger.error("ssh-bootstrap: pod %s never exposed ssh host within 10min", pod_id)
            return

        bootstrap = _render_bootstrap_script(
            model_id=model_id,
            hf_repo=hf_repo,
            gateway_url=self.gateway_public_url,
            worker_token=worker_token,
        )

        # SSH itself may take another ~30s after status flips RUNNING.
        ssh_deadline = time.time() + 300
        last_err: Optional[Exception] = None
        while time.time() < ssh_deadline:
            try:
                async with asyncssh.connect(
                    host,
                    port=port,
                    username=ssh_user,
                    client_keys=[ssh_key_path],
                    known_hosts=None,
                ) as conn:
                    # Write script to /root/bootstrap.sh and run via nohup.
                    await conn.run(
                        "cat > /root/bootstrap.sh && chmod +x /root/bootstrap.sh",
                        input=bootstrap,
                        check=True,
                    )
                    await conn.run(
                        "nohup /root/bootstrap.sh > /var/log/sgpu-bootstrap.log 2>&1 < /dev/null &",
                        check=True,
                    )
                    logger.info("ssh-bootstrap: launched on %s:%d for pod=%s", host, port, pod_id)
                    return
            except Exception as e:
                last_err = e
                await asyncio.sleep(10)
        logger.error(
            "ssh-bootstrap: failed to connect to %s:%d after 5min (last: %s)",
            host, port, last_err,
        )

    async def shutdown(self) -> None:
        # Don't terminate pods on gateway shutdown — that would scale-to-zero
        # everything just because we restarted. Terminate only when the
        # autoscaler explicitly says so.
        if self._owns_client:
            await self._client.aclose()
