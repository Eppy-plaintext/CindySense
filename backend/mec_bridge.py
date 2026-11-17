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
from backend.tinyiot import TinyIoTClient, TinyIoTError


class MecBridgeConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class MecBridgeConfig:
    enabled: bool = False
    origin: str = ''
    ae: str = 'MECBridge_AE'
    wlan_container: str = 'WLANInformation'

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
        }


class MecBridge:
    """Synchronize one MEC 028 snapshot into a dedicated oneM2M container."""

    def __init__(self, mec_client: MecWlanClient, tinyiot: TinyIoTClient,
                 config: MecBridgeConfig, *, clock: Callable[[], datetime] | None = None):
        config.validate()
        self.mec_client = mec_client
        self.tinyiot = tinyiot
        self.config = config
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

        try:
            payload = await self.mec_client.fetch_access_points()
            access_points = map_wlan_access_points(payload)
        except MecClientError as exc:
            return MecBridgeResult(False, request_id, f'mec_{exc.code}', exc.message,
                                   resource_path, status_code=exc.status_code)
        except MecWlanMappingError as exc:
            return MecBridgeResult(False, request_id, exc.code, str(exc),
                                   resource_path, status_code=502)

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
                                   resource_path, name, status_code=exc.status_code)

        return MecBridgeResult(True, request_id, 'published_and_verified',
                               'MEC WLAN snapshot published and verified by readback.',
                               resource_path, name, created=created, verified=True,
                               access_point_count=len(access_points),
                               total_associated_stations=content['totalAssociatedStations'])
