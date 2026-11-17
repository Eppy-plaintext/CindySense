"""Mapping and validation for ETSI MEC 028 WLAN AP data."""

from __future__ import annotations

from typing import Any


class MecWlanMappingError(ValueError):
    def __init__(self, message: str, *, index: int | None = None):
        super().__init__(message)
        self.code = 'invalid_wlan_access_point'
        self.index = index

    def as_dict(self) -> dict[str, Any]:
        result = {'code': self.code, 'message': str(self)}
        if self.index is not None:
            result['index'] = self.index
        return result


SIMULATED_SOURCE = 'ETSI MEC Sandbox simulated network data'


def map_wlan_access_points(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return validated, source-unit-preserving AP summaries."""
    if not isinstance(payload, list):
        raise MecWlanMappingError('MEC WLAN payload must be a list.')

    mapped = []
    for index, access_point in enumerate(payload):
        if not isinstance(access_point, dict):
            raise MecWlanMappingError('Access point must be an object.', index=index)
        ap_id = access_point.get('apId')
        if not isinstance(ap_id, dict) or not isinstance(ap_id.get('bssid'), str) or not ap_id['bssid'].strip():
            raise MecWlanMappingError('Access point is missing apId.bssid.', index=index)

        bss_load = access_point.get('bssLoad')
        if not isinstance(bss_load, dict):
            raise MecWlanMappingError('Access point is missing bssLoad.', index=index)
        station_count = bss_load.get('staCount')
        if not isinstance(station_count, int) or isinstance(station_count, bool) or station_count < 0:
            raise MecWlanMappingError('Access point has an invalid bssLoad.staCount.', index=index)

        item = {
            'bssid': ap_id['bssid'],
            'stationCount': station_count,
            'source': SIMULATED_SOURCE,
        }
        optional_fields = {
            'channelUtilization': ('bssLoad', 'channelUtilization'),
            'availableAdmissionCapacity': ('bssLoad', 'availAdmCap'),
            'location': ('access_point', 'apLocation'),
        }
        for target, (parent, source) in optional_fields.items():
            source_object = access_point if parent == 'access_point' else bss_load
            if source in source_object:
                item[target] = source_object[source]
        mapped.append(item)
    return mapped
