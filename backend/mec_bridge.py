"""One-shot ETSI MEC 028 to oneM2M interworking bridge.

The bridge is deliberately isolated from webcam, workout, CindyPT, and dashboard
flows. It performs no work at import time and has no polling or background loop.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from backend.mec_client import MecClientError, MecWlanClient
from backend.mec_mapping import MecWlanMappingError, SIMULATED_SOURCE, map_wlan_access_points
from backend.mec_registry import MecRegistryError, MecServiceRegistry
from backend.tinyiot import TinyIoTClient, TinyIoTError


class MecBridgeConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class MecBridgeConfig:
    enabled: bool = False
    origin: str = ''
    ae: str = 'MECBridge_AE'
    wlan_container: str = 'WLANInformation'
    dynamic_service_discovery: bool = False
    allow_configured_fallback: bool = False

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.origin:
            raise MecBridgeConfigurationError('MEC bridge origin is not configured.')
        for value, label in ((self.ae, 'MEC bridge AE'),
                             (self.wlan_container, 'MEC bridge WLAN container')):
            if not value or '/' in value or value in ('.', '..'):
                raise MecBridgeConfigurationError(f'{label} name is invalid.')


@dataclass(frozen=True)
class MecBridgeResult:
    success: bool
    request_id: str
    code: str
    message: str
    resource_path: str
    content_instance_name: str | None = None
    created: bool = False
    verified: bool = False
    access_point_count: int = 0
    total_associated_stations: int = 0
    source: str = SIMULATED_SOURCE
    status_code: int = 200
    discovery_status: str = 'not_requested'
    discovery_source: str = 'configured'
    selected_service_name: str | None = None
    selected_service_instance_id: str | None = None
    discovery_error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'success': self.success,
            'request_id': self.request_id,
            'code': self.code,
            'message': self.message,
            'resource_path': self.resource_path,
            'content_instance_name': self.content_instance_name,
            'created': self.created,
            'verified': self.verified,
            'access_point_count': self.access_point_count,
            'total_associated_stations': self.total_associated_stations,
            'source': self.source,
            'status_code': self.status_code,
            'discovery_status': self.discovery_status,
            'discovery_source': self.discovery_source,
            'selected_service_name': self.selected_service_name,
            'selected_service_instance_id': self.selected_service_instance_id,
            'discovery_error': self.discovery_error,
        }


class MecBridge:
    """Synchronize one MEC 028 snapshot into a dedicated oneM2M container."""

    def __init__(self, mec_client: MecWlanClient, tinyiot: TinyIoTClient,
                 config: MecBridgeConfig, *, clock: Callable[[], datetime] | None = None,
                 registry: MecServiceRegistry | None = None):
        config.validate()
        self.mec_client = mec_client
        self.tinyiot = tinyiot
        self.config = config
        self.registry = registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def wlan_resource_path(self) -> str:
        return f'{self.config.ae}/{self.config.wlan_container}'

    @staticmethod
    def content_instance_name(request_id: str) -> str:
        """Create a deterministic valid CIN name for idempotent retry."""
        digest = hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:32]
        return f'mec-{digest}'

    async def synchronize_once(self, request_id: str | None = None) -> MecBridgeResult:
        request_id = request_id or uuid4().hex
        resource_path = self.wlan_resource_path
        if not self.config.enabled:
            return MecBridgeResult(False, request_id, 'bridge_disabled',
                                   'MEC bridge is disabled.', resource_path, status_code=503)

        mec_client = self.mec_client
        discovery_status = 'not_requested'
        discovery_source = 'configured'
        discovery_error = None
        selected_service_name = None
        selected_service_instance_id = None
        if self.config.dynamic_service_discovery:
            if self.registry is None:
                return MecBridgeResult(
                    False, request_id, 'mec_service_discovery_not_configured',
                    'MEC service discovery is enabled but no registry is configured.',
                    resource_path, status_code=503,
                    discovery_status='not_configured',
                    discovery_source='none',
                    discovery_error={'code': 'registry_not_configured'})
            try:
                selection = await self.registry.select_wlan()
                discovery_status = selection.diagnostics.status
                discovery_source = selection.diagnostics.source
                discovery_error = selection.diagnostics.error
                if not selection.dynamic_discovery and not self.config.allow_configured_fallback:
                    return MecBridgeResult(
                        False, request_id, 'mec_service_discovery_fallback_disabled',
                        'MEC service discovery failed and configured fallback is disabled.',
                        resource_path, status_code=503,
                        discovery_status=discovery_status,
                        discovery_source=discovery_source,
                        discovery_error=discovery_error)
                if not selection.endpoint:
                    return MecBridgeResult(
                        False, request_id, 'mec_service_discovery_no_endpoint',
                        'MEC service discovery returned no usable WLAN endpoint.',
                        resource_path, status_code=502,
                        discovery_status=discovery_status,
                        discovery_source=discovery_source,
                        discovery_error=discovery_error)
                if selection.service is not None:
                    selected_service_name = selection.service.name
                    selected_service_instance_id = selection.service.instance_id
                mec_client = self.mec_client.with_base_url(selection.endpoint)
            except MecRegistryError as exc:
                return MecBridgeResult(
                    False, request_id, f'mec_discovery_{exc.code}', exc.message,
                    resource_path, status_code=exc.status_code,
                    discovery_status='error', discovery_source='mec011',
                    discovery_error=exc.as_dict())

        try:
            payload = await mec_client.fetch_access_points()
            access_points = map_wlan_access_points(payload)
        except MecClientError as exc:
            return MecBridgeResult(False, request_id, f'mec_{exc.code}', exc.message,
                                   resource_path, status_code=exc.status_code,
                                   discovery_status=discovery_status,
                                   discovery_source=discovery_source,
                                   discovery_error=discovery_error,
                                   selected_service_name=selected_service_name,
                                   selected_service_instance_id=selected_service_instance_id)
        except MecWlanMappingError as exc:
            return MecBridgeResult(False, request_id, exc.code, str(exc),
                                   resource_path, status_code=502,
                                   discovery_status=discovery_status,
                                   discovery_source=discovery_source,
                                   discovery_error=discovery_error,
                                   selected_service_name=selected_service_name,
                                   selected_service_instance_id=selected_service_instance_id)

        content = {
            'schema': 'cindysense.mec.wlan.v1',
            'requestId': request_id,
            'observedAt': self._clock().astimezone(timezone.utc).isoformat(),
            'source': SIMULATED_SOURCE,
            'accessPointCount': len(access_points),
            'totalAssociatedStations': sum(item['stationCount'] for item in access_points),
            'accessPoints': access_points,
        }
        name = self.content_instance_name(request_id)
        try:
            created = await self.tinyiot.create_named_verified_content(
                resource_path, self.config.origin, name, content)
        except TinyIoTError as exc:
            return MecBridgeResult(False, request_id, f'onem2m_{exc.code}', str(exc),
                                   resource_path, name, status_code=exc.status_code,
                                   discovery_status=discovery_status,
                                   discovery_source=discovery_source,
                                   discovery_error=discovery_error,
                                   selected_service_name=selected_service_name,
                                   selected_service_instance_id=selected_service_instance_id)

        return MecBridgeResult(True, request_id, 'published_and_verified',
                               'MEC WLAN snapshot published and verified by readback.',
                               resource_path, name, created=created, verified=True,
                               access_point_count=len(access_points),
                               total_associated_stations=content['totalAssociatedStations'],
                               discovery_status=discovery_status,
                               discovery_source=discovery_source,
                               discovery_error=discovery_error,
                               selected_service_name=selected_service_name,
                               selected_service_instance_id=selected_service_instance_id)
