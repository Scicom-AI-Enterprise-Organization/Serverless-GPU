"""Fail-fast validation when PROVIDER=primeintellect.

Verifies the gateway refuses to boot if PI envs are missing or stubbed —
saves operators an hour of confused debugging when the autoscaler quietly
401s every provision.
"""
import asyncio

import pytest


@pytest.fixture
def restore_env(monkeypatch):
    yield monkeypatch


@pytest.mark.asyncio
async def test_pi_provider_with_missing_envs_refuses_to_boot(restore_env):
    """PROVIDER=primeintellect + no PI_API_KEY → RuntimeError on lifespan startup."""
    restore_env.setenv("AUTOSCALER", "1")
    restore_env.setenv("PROVIDER", "primeintellect")
    restore_env.delenv("PI_API_KEY", raising=False)
    restore_env.delenv("PI_CUSTOM_TEMPLATE_ID", raising=False)
    restore_env.delenv("GATEWAY_PUBLIC_URL", raising=False)

    from gateway.main import lifespan, app

    with pytest.raises(RuntimeError, match="PROVIDER=primeintellect requires"):
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_pi_provider_with_stub_values_refuses_to_boot(restore_env):
    """`replace-me` from .env.example → RuntimeError, not silent acceptance."""
    restore_env.setenv("AUTOSCALER", "1")
    restore_env.setenv("PROVIDER", "primeintellect")
    restore_env.setenv("PI_API_KEY", "replace-me")
    restore_env.setenv("PI_CUSTOM_TEMPLATE_ID", "replace-me")
    restore_env.setenv("GATEWAY_PUBLIC_URL", "https://example.com")

    from gateway.main import lifespan, app

    with pytest.raises(RuntimeError, match="PI_API_KEY"):
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_fake_provider_doesnt_need_pi_envs(restore_env):
    """PROVIDER=fake should boot fine without any PI env."""
    restore_env.setenv("AUTOSCALER", "1")
    restore_env.setenv("PROVIDER", "fake")
    restore_env.delenv("PI_API_KEY", raising=False)
    restore_env.delenv("PI_CUSTOM_TEMPLATE_ID", raising=False)
    restore_env.setenv("GATEWAY_URL_FOR_PROVIDER", "http://127.0.0.1:9999")

    from gateway.main import lifespan, app

    # Should NOT raise
    async with lifespan(app):
        assert app.state.provider is not None
        assert app.state.provider.name == "fake"


@pytest.mark.asyncio
async def test_pi_provider_default_cloud_id_is_runpod():
    """PI_CLOUD_ID empty → default to 'runpod' (verified working in PI smoke test)."""
    import os
    from gateway.pi_provider import PrimeIntellectProvider

    p = PrimeIntellectProvider(
        api_key="test-key",
        custom_template_id="tmpl-x",
        gateway_public_url="https://example.com",
    )
    assert p.cloud_id == "runpod", f"expected default cloud_id='runpod', got {p.cloud_id!r}"
