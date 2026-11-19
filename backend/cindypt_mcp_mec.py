"""Optional read-only MCP tools for the existing MEC discovery and WLAN clients."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import settings
from .mec_client import MecClientError, MecWlanClient, MecWlanConfig
from .mec_registry import MecServiceDiscoveryConfig, MecServiceRegistry, MecRegistryError


@dataclass(frozen=True)
class MecToolConfig:
    enabled: bool
    service_discovery_enabled: bool
    service_mgmt_api_base_url: str
    wlan_api_base_url: str
    timeout_seconds: float
    discovery_ttl_seconds: float
    allow_configured_fallback: bool

    @classmethod
    def from_settings(cls) -> 'MecToolConfig':
        return cls(settings.mec_enabled, settings.mec_service_discovery_enabled,
                   settings.mec_service_mgmt_api_base_url, settings.mec_wlan_api_base_url,
                   settings.mec_request_timeout_seconds, settings.mec_service_discovery_ttl_seconds,
                   settings.mec_allow_configured_fallback)


def _unavailable(exc: MecClientError | MecRegistryError) -> dict[str, Any]:
    return {'ok': False, 'available': False, 'error': exc.as_dict()}


def create_mec_mcp_server(config: MecToolConfig) -> MCPServer:
    """Create a server with no background polling and no oneM2M writes."""
    server = MCPServer('cindysense-mec', version='1.0.0',
                       instructions='Read-only MEC 011 discovery and MEC 028 WLAN information tools.')

    def registry() -> MecServiceRegistry:
        return MecServiceRegistry(MecServiceDiscoveryConfig(
            enabled=config.enabled and config.service_discovery_enabled,
            api_base_url=config.service_mgmt_api_base_url,
            fallback_wlan_url=config.wlan_api_base_url if config.allow_configured_fallback else '',
            ttl_seconds=config.discovery_ttl_seconds))

    @server.tool()
    async def discover_wlan_service(refresh: bool = False) -> dict[str, Any]:
        """Discover the one trusted active MEC 028 WLAN service, or explain why it is unavailable."""
        try:
            selection = await registry().select_wlan(refresh=refresh)
            return {'ok': True, 'available': True, **selection.as_dict()}
        except MecRegistryError as exc:
            return _unavailable(exc)

    @server.tool()
    async def get_wlan_access_points() -> dict[str, Any]:
        """Read MEC 028 access-point information when MEC access is explicitly enabled."""
        try:
            access_points = await MecWlanClient(MecWlanConfig(
                enabled=config.enabled, api_base_url=config.wlan_api_base_url,
                timeout_seconds=config.timeout_seconds)).fetch_access_points()
            return {'ok': True, 'available': True, 'accessPoints': access_points}
        except MecClientError as exc:
            return _unavailable(exc)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description='Run the optional CindySense MEC MCP server.')
    parser.add_argument('--transport', choices=('stdio',), default='stdio')
    parser.parse_args()
    create_mec_mcp_server(MecToolConfig.from_settings()).run(transport='stdio')


if __name__ == '__main__':
    main()
