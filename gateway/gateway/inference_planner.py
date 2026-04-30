"""Pick a GPU tier for a HuggingFace model.

Heuristic: params × bytes_per_param × 1.3 (KV cache + activation overhead).
We never expose the raw GPU model name to non-admins — only `tier_label`.

Sources of param count, in order of preference:
  1. HF API `/api/models/{repo}` `safetensors.parameters.total`
  2. HF API `/api/models/{repo}` `siblings`/`config.json` heuristics
  3. Repo-name regex (e.g. "qwen2.5-0.5b" → 0.5B)

When all fail, we default to a conservative 24GB consumer tier and log it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("gateway.inference_planner")

HF_API = "https://huggingface.co/api/models"

# (max VRAM in GB, internal gpu_type, user-visible tier label)
_TIERS: list[tuple[int, str, str]] = [
    (24, "RTX3090", "consumer-24gb"),
    (48, "L40S", "datacenter-48gb"),
    (80, "A100", "datacenter-80gb"),
    (128, "H100", "datacenter-h100"),
]


@dataclass
class GpuPick:
    vram_gb: int        # estimated VRAM needed
    gpu_type: str       # internal — matches PI _GPU_NAME_MAP keys
    tier_label: str     # user-visible

    def to_dict(self) -> dict:
        return {"vram_gb": self.vram_gb, "gpu_type": self.gpu_type, "tier_label": self.tier_label}


_PARAM_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*([bm])\b", re.IGNORECASE)


def _params_from_repo_name(repo: str) -> Optional[float]:
    """Extract param count from repo name like 'Qwen/Qwen2.5-0.5B-Instruct'.

    Returns the count in *raw units* (e.g. 0.5e9 for 0.5B). None if no match.
    """
    m = _PARAM_REGEX.search(repo.split("/")[-1])
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).lower()
    return n * (1e9 if unit == "b" else 1e6)


def _bytes_per_param(repo: str) -> int:
    repo_lower = repo.lower()
    if any(s in repo_lower for s in ("-awq", "-gptq", "-int4", "-4bit")):
        return 1   # ~4-bit quantized, but we use 1 for safety on KV cache
    if any(s in repo_lower for s in ("-int8", "-fp8", "-8bit")):
        return 1
    return 2  # fp16 / bf16


async def _fetch_params_from_api(repo: str, *, timeout: float = 8.0) -> Optional[float]:
    """Best-effort HF API call. Returns raw param count or None on any error."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{HF_API}/{repo}")
            if r.status_code != 200:
                return None
            data = r.json()
            # Newer HF responses include `safetensors.parameters` as a dict of dtype -> count.
            sf = data.get("safetensors") or {}
            params = sf.get("parameters")
            if isinstance(params, dict):
                total = sum(int(v) for v in params.values() if isinstance(v, (int, float)))
                if total > 0:
                    return float(total)
            elif isinstance(params, (int, float)) and params > 0:
                return float(params)
    except Exception as e:
        logger.warning("HF param lookup failed for %s: %s", repo, e)
    return None


def _pick_tier(vram_gb: int) -> tuple[str, str]:
    for cap, gpu_type, label in _TIERS:
        if vram_gb <= cap:
            return gpu_type, label
    return _TIERS[-1][1], _TIERS[-1][2]


async def recommend_gpu(repo: str) -> GpuPick:
    """Estimate VRAM and pick the smallest tier that fits."""
    params = await _fetch_params_from_api(repo)
    if params is None:
        params = _params_from_repo_name(repo)
    if params is None:
        # Conservative fallback. 8B-ish at fp16 → ~20GB.
        logger.info("no param count for %s, defaulting to 24GB tier", repo)
        return GpuPick(vram_gb=24, gpu_type="RTX3090", tier_label="consumer-24gb")

    bpp = _bytes_per_param(repo)
    raw_gb = (params * bpp * 1.3) / 1e9
    # Round up, minimum 1GB so tiny models still get a real tier.
    vram_gb = max(1, int(raw_gb + 0.999))
    gpu_type, tier_label = _pick_tier(vram_gb)
    logger.info(
        "gpu pick: repo=%s params=%.2fB bpp=%d raw=%.1fGB → %s (%s)",
        repo, params / 1e9, bpp, raw_gb, gpu_type, tier_label,
    )
    return GpuPick(vram_gb=vram_gb, gpu_type=gpu_type, tier_label=tier_label)
