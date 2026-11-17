"""Read-only ETSI MEC 011 service discovery and MEC 028 selection."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx


class MecRegistryErrorCode(StrEnum):
    DISABLED = 'service_discovery_disabled'
    INVALID_CONFIGURATION = 'invalid_configuration'
    HTTP_ERROR = 'service_discovery_http_error'
    TIMEOUT = 'service_discovery_timeout'
    CONNECTION_ERROR = 'service_discovery_connection_error'
    INVALID_CONTENT_TYPE = 'service_discovery_invalid_content_type'
    INVALID_JSON = 'service_discovery_invalid_json'
    INVALID_RESPONSE = 'service_discovery_invalid_response'
    SERVICE_NOT_FOUND = 'mec028_service_not_found'
    SERVICE_INACTIVE = 'mec028_service_inactive'
    AMBIGUOUS_SERVICE = 'mec028_service_ambiguous'
    INVALID_ENDPOINT = 'mec028_endpoint_invalid'
    UNTRUSTED_ENDPOINT = 'mec028_endpoint_untrusted'


class MecRegistryError(Exception):
    def __init__(self, code: MecRegistryErrorCode | str, message: str, status_code: int,
                 *, retryable: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {'code': self.code, 'message': self.message,
                'status_code': self.status_code, 'retryable': self.retryable}


@dataclass(frozen=True)
class MecService:
    instance_id: str
    name: str
    category_id: str
    category_name: str | None
    state: str
    endpoint_uris: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {'endpoint_uris': list(self.endpoint_uris)}


@dataclass(frozen=True)
class MecServiceDiscoveryConfig:
    enabled: bool = False
    api_base_url: str = ''
    fallback_wlan_url: str = ''
    ttl_seconds: float = 30.0
    trusted_hosts: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.ttl_seconds < 0:
            raise MecRegistryError(MecRegistryErrorCode.INVALID_CONFIGURATION,
                                   'MEC service discovery TTL cannot be negative.', 503)
        if self.enabled:
            self._validate_https_url(self.api_base_url, 'service-management API')
        if self.fallback_wlan_url:
            self._validate_https_url(self.fallback_wlan_url, 'configured WLAN fallback')
        if any(not isinstance(host, str) or not host.strip() for host in self.trusted_hosts):
            raise MecRegistryError(MecRegistryErrorCode.INVALID_CONFIGURATION,
                                   'Trusted MEC endpoint hosts are invalid.', 503)

    @staticmethod
    def _validate_https_url(value: str, label: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise MecRegistryError(MecRegistryErrorCode.INVALID_CONFIGURATION,
                                   f'Enabled {label} requires a credential-free HTTPS URL.', 503)
        if parsed.query or parsed.fragment:
            raise MecRegistryError(MecRegistryErrorCode.INVALID_CONFIGURATION,
                                   f'Configured {label} URL cannot contain query or fragment.', 503)


@dataclass(frozen=True)
class MecServiceDiscoveryDiagnostics:
    status: str
    source: str
    service_count: int = 0
    selected_instance_id: str | None = None
    selected_endpoint: str | None = None
    from_cache: bool = False
    fallback_used: bool = False
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MecServiceSelection:
    service: MecService | None
    endpoint: str | None
    dynamic_discovery: bool
    diagnostics: MecServiceDiscoveryDiagnostics

    def as_dict(self) -> dict[str, Any]:
        return {
            'service': self.service.as_dict() if self.service else None,
            'endpoint': self.endpoint,
            'dynamic_discovery': self.dynamic_discovery,
            'diagnostics': self.diagnostics.as_dict(),
        }


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    services: tuple[MecService, ...]


class MecServiceRegistry:
    """Discover MEC services once per TTL and select the active MEC 028 WAI."""

    WLAN_CATEGORY_ID = 'waiId'

    def __init__(self, config: MecServiceDiscoveryConfig,
                 client: httpx.AsyncClient | None = None, *, clock=time.monotonic,
                 headers: Mapping[str, str] | None = None):
        config.validate()
        self.config = config
        self._client = client
        self._clock = clock
        self._headers = dict(headers or {})
        self._cache: _CacheEntry | None = None
        self._lock = asyncio.Lock()

    @property
    def services_url(self) -> str:
        parsed = urlsplit(self.config.api_base_url)
        path = parsed.path.rstrip('/')
        if not path.endswith('/services'):
            path += '/services'
        return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))

    def invalidate(self) -> None:
        self._cache = None

    def cache_status(self) -> dict[str, Any]:
        entry = self._cache
        remaining = max(0.0, round(entry.expires_at - self._clock(), 3)) if entry else 0.0
        return {'ttl_seconds': self.config.ttl_seconds,
                'cached': bool(entry and remaining > 0),
                'service_count': len(entry.services) if entry and remaining > 0 else 0,
                'ttl_remaining_seconds': remaining}

    async def refresh(self) -> tuple[MecService, ...]:
        self.invalidate()
        return await self.discover_services(refresh=True)

    async def discover_services(self, *, refresh: bool = False) -> tuple[MecService, ...]:
        if not self.config.enabled:
            raise MecRegistryError(MecRegistryErrorCode.DISABLED,
                                   'MEC service discovery is disabled.', 503)
        async with self._lock:
            now = self._clock()
            if not refresh and self._cache and self._cache.expires_at > now:
                return self._cache.services
            try:
                payload = await self._get_services()
                services = self._parse_services(payload)
            except MecRegistryError:
                raise
            self._cache = _CacheEntry(now + self.config.ttl_seconds, services)
            return services

    async def select_wlan(self, *, refresh: bool = False) -> MecServiceSelection:
        try:
            services = await self.discover_services(refresh=refresh)
            active = [service for service in services
                      if service.category_id == self.WLAN_CATEGORY_ID and service.state == 'ACTIVE']
            if len(active) > 1:
                raise MecRegistryError(MecRegistryErrorCode.AMBIGUOUS_SERVICE,
                                       'Multiple active MEC 028 WLAN services were discovered.', 502)
            if not active:
                has_inactive = any(service.category_id == self.WLAN_CATEGORY_ID for service in services)
                code = (MecRegistryErrorCode.SERVICE_INACTIVE if has_inactive
                        else MecRegistryErrorCode.SERVICE_NOT_FOUND)
                raise MecRegistryError(code, 'No active MEC 028 WLAN service was discovered.', 404)
            service = active[0]
            endpoint = self._select_endpoint(service.endpoint_uris)
            return MecServiceSelection(
                service, endpoint, True,
                MecServiceDiscoveryDiagnostics('discovered', 'mec011', len(services),
                                               service.instance_id, endpoint,
                                               self.cache_status()['cached']))
        except MecRegistryError as exc:
            fallback = self._validated_fallback()
            if fallback is None:
                raise
            diagnostics = MecServiceDiscoveryDiagnostics(
                'fallback', 'configured_fallback',
                error=exc.as_dict(), fallback_used=True)
            return MecServiceSelection(None, fallback, False, diagnostics)

    async def _get_services(self) -> Any:
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=10.0, verify=True,
                                             follow_redirects=False) as client:
                    response = await client.get(self.services_url,
                                                headers={'Accept': 'application/json', **self._headers})
            else:
                response = await self._client.get(
                    self.services_url, headers={'Accept': 'application/json', **self._headers})
        except httpx.TimeoutException as exc:
            raise MecRegistryError(MecRegistryErrorCode.TIMEOUT,
                                   'MEC service discovery timed out.', 504, retryable=True) from exc
        except httpx.RequestError as exc:
            raise MecRegistryError(MecRegistryErrorCode.CONNECTION_ERROR,
                                   'MEC service discovery could not be reached.', 503,
                                   retryable=True) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise MecRegistryError(MecRegistryErrorCode.HTTP_ERROR,
                                   f'MEC service discovery returned HTTP {response.status_code}.',
                                   response.status_code,
                                   retryable=response.status_code >= 500 or response.status_code == 429)
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            raise MecRegistryError(MecRegistryErrorCode.INVALID_CONTENT_TYPE,
                                   'MEC service discovery returned a non-JSON response.', 502)
        try:
            return response.json()
        except ValueError as exc:
            raise MecRegistryError(MecRegistryErrorCode.INVALID_JSON,
                                   'MEC service discovery returned malformed JSON.', 502) from exc

    @classmethod
    def _parse_services(cls, payload: Any) -> tuple[MecService, ...]:
        if not isinstance(payload, list):
            raise MecRegistryError(MecRegistryErrorCode.INVALID_RESPONSE,
                                   'MEC service discovery response must be an array.', 502)
        services = []
        for item in payload:
            if not isinstance(item, dict):
                raise MecRegistryError(MecRegistryErrorCode.INVALID_RESPONSE,
                                       'MEC service discovery contains a non-object service.', 502)
            category = item.get('serCategory')
            transport = item.get('transportInfo')
            endpoint = transport.get('endpoint') if isinstance(transport, dict) else None
            uris = endpoint.get('uris') if isinstance(endpoint, dict) else None
            required = ('serInstanceId', 'serName', 'state')
            if (any(not isinstance(item.get(key), str) or not item[key].strip() for key in required) or
                    not isinstance(category, dict) or not isinstance(category.get('id'), str) or
                    not category['id'].strip() or not isinstance(uris, list) or
                    any(not isinstance(uri, str) or not uri.strip() for uri in uris)):
                raise MecRegistryError(MecRegistryErrorCode.INVALID_RESPONSE,
                                       'MEC service discovery contains an invalid service representation.', 502)
            category_name = category.get('name')
            if category_name is not None and not isinstance(category_name, str):
                raise MecRegistryError(MecRegistryErrorCode.INVALID_RESPONSE,
                                       'MEC service category name is invalid.', 502)
            services.append(MecService(item['serInstanceId'], item['serName'], category['id'],
                                       category_name, item['state'], tuple(uris)))
        return tuple(services)

    def _select_endpoint(self, endpoints: tuple[str, ...]) -> str:
        trusted = self._trusted_hosts()
        valid = []
        has_external_https = False
        for endpoint in endpoints:
            parsed = urlsplit(endpoint)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                continue
            if parsed.query or parsed.fragment:
                continue
            if parsed.hostname.lower() not in trusted:
                has_external_https = True
                continue
            valid.append(endpoint)
        if len(valid) != 1:
            code = (MecRegistryErrorCode.UNTRUSTED_ENDPOINT if has_external_https
                    else MecRegistryErrorCode.INVALID_ENDPOINT)
            raise MecRegistryError(code, 'MEC 028 service endpoint is not a single trusted HTTPS URL.', 502)
        return valid[0]

    def _trusted_hosts(self) -> set[str]:
        configured = {host.lower().strip() for host in self.config.trusted_hosts}
        base_host = urlsplit(self.config.api_base_url).hostname
        if base_host:
            configured.add(base_host.lower())
        return configured

    def _validated_fallback(self) -> str | None:
        if not self.config.fallback_wlan_url:
            return None
        parsed = urlsplit(self.config.fallback_wlan_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.query or parsed.fragment or parsed.hostname.lower() not in self._trusted_hosts():
            return None
        return self.config.fallback_wlan_url

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
