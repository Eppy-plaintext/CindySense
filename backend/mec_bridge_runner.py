"""Explicit one-shot runner for the existing optional MEC-to-oneM2M bridge.

This module is not imported by FastAPI and never starts a polling loop.  An
operator must invoke it explicitly after configuring the already documented
MEC bridge settings and a dedicated oneM2M bridge origin.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from .config import settings
from .mec_bridge import MecBridge, MecBridgeConfig, MecBridgeResult
from .mec_client import MecWlanClient, MecWlanConfig
from .mec_registry import MecServiceDiscoveryConfig, MecServiceRegistry
from .tinyiot import TinyIoTClient


def build_bridge(config: Any = settings, *, tinyiot: TinyIoTClient | None = None,
                 mec_client: MecWlanClient | None = None,
                 registry: MecServiceRegistry | None = None) -> MecBridge:
    """Build the pre-existing bridge from Settings without executing network I/O."""
    bridge_config = MecBridgeConfig(
        enabled=config.mec_bridge_enabled,
        origin=config.mec_bridge_origin,
        ae=config.mec_bridge_ae,
        wlan_container=config.mec_bridge_wlan_container,
        dynamic_service_discovery=config.mec_service_discovery_enabled,
        allow_configured_fallback=config.mec_allow_configured_fallback,
    )
    bridge_config.validate()
    if mec_client is None:
        mec_client = MecWlanClient(MecWlanConfig(
            enabled=config.mec_enabled,
            api_base_url=config.mec_wlan_api_base_url,
            timeout_seconds=config.mec_request_timeout_seconds,
        ))
    if bridge_config.dynamic_service_discovery and registry is None:
        registry = MecServiceRegistry(MecServiceDiscoveryConfig(
            enabled=True,
            api_base_url=config.mec_service_mgmt_api_base_url,
            fallback_wlan_url=(config.mec_wlan_api_base_url
                               if config.mec_allow_configured_fallback else ''),
            ttl_seconds=config.mec_service_discovery_ttl_seconds,
        ))
    tinyiot = tinyiot or TinyIoTClient(config.base_url, httpx.AsyncClient(timeout=5.0))
    return MecBridge(mec_client, tinyiot, bridge_config, registry=registry)


async def synchronize_once(request_id: str | None = None, *, config: Any = settings) -> MecBridgeResult:
    """Execute one explicit bridge attempt and always release the oneM2M client."""
    bridge = build_bridge(config)
    try:
        return await bridge.synchronize_once(request_id)
    finally:
        client = bridge.tinyiot._client
        if client is not None:
            await client.aclose()
        if bridge.registry is not None:
            await bridge.registry.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description='Run one explicit CindySense MEC bridge attempt.')
    parser.add_argument('--request-id', default=None,
                        help='Optional idempotency key for a retry of the same snapshot request.')
    args = parser.parse_args()
    result = asyncio.run(synchronize_once(args.request_id))
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == '__main__':
    main()
