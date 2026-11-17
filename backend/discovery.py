"""Cached, permission-aware oneM2M resource discovery.

This module is intentionally not wired into application flows yet.  Discovery
uses the configured discovery origin, while fallback verification uses the
origin that will perform the eventual operation.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from .tinyiot import TinyIoTClient, TinyIoTError


AE_TYPE = 2
CONTAINER_TYPE = 3
SUBSCRIPTION_TYPE = 23
_TYPE_KEYS = {AE_TYPE: "m2m:ae", CONTAINER_TYPE: "m2m:cnt", SUBSCRIPTION_TYPE: "m2m:sub"}


class DiscoveryStatus(StrEnum):
    DISCOVERED = "discovered"
    FALLBACK = "fallback"
    MISSING = "missing"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    INVALID_RESPONSE = "invalid_response"
    INVALID_REQUEST = "invalid_request"
    ERROR = "error"


@dataclass(frozen=True)
class DiscoveryDiagnostics:
    status: DiscoveryStatus
    scope: str
    resource_type: int
    requested_name: str | None = None
    path: str | None = None
    source: str | None = None
    verified: bool = False
    from_cache: bool = False
    fallback_attempted: bool = False
    error_code: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass(frozen=True)
class DiscoveryResult:
    """A structured discovery result; ``path`` is never unvalidated."""

    path: str | None
    resources: tuple[str, ...]
    diagnostics: DiscoveryDiagnostics

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "resources": list(self.resources),
            "diagnostics": self.diagnostics.as_dict(),
        }


@dataclass
class _CacheEntry:
    expires_at: float
    resources: tuple[str, ...]


class ResourceResolver:
    """Resolve oneM2M AEs, containers, and subscriptions with safe fallback.

    ``discovery_origin`` is only used for ``fu=1`` requests.  Callers pass the
    operation origin to :meth:`resolve` so a fallback is accepted only after a
    normal authenticated retrieve succeeds under that origin.
    """

    def __init__(self, client: TinyIoTClient, discovery_origin: str, *, ttl_seconds: float = 30.0,
                 clock=time.monotonic):
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self.client = client
        self.discovery_origin = discovery_origin
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._cache: dict[tuple[str, int], _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def discover_aes(self, *, refresh: bool = False) -> DiscoveryResult:
        return await self._discover("", AE_TYPE, refresh=refresh)

    async def discover_containers(self, ae_scope: str, *, refresh: bool = False) -> DiscoveryResult:
        scope = self._validate_scope(ae_scope)
        if scope is None:
            return self._invalid_result(ae_scope, CONTAINER_TYPE)
        return await self._discover(scope, CONTAINER_TYPE, refresh=refresh)

    async def discover_subscriptions(self, ae_scope: str, container_name: str,
                                     *, refresh: bool = False) -> DiscoveryResult:
        scope = self._validate_scope(ae_scope)
        if scope is None or not self._valid_name(container_name):
            return self._invalid_result(ae_scope, SUBSCRIPTION_TYPE, container_name)
        return await self._discover(f"{scope}/{container_name}", SUBSCRIPTION_TYPE, refresh=refresh)

    async def resolve(self, *, ae_scope: str, name: str, resource_type: int,
                      operation_origin: str, fallback_path: str | None = None,
                      refresh: bool = False) -> DiscoveryResult:
        scope = self._validate_scope(ae_scope)
        if scope is None or resource_type not in _TYPE_KEYS or not self._valid_name(name):
            return self._invalid_result(ae_scope, resource_type, name)
        if not operation_origin:
            return self._result(scope, resource_type, name, None, DiscoveryStatus.INVALID_REQUEST,
                                message="An operation origin is required to verify a fallback.")

        parent = "" if resource_type == AE_TYPE else scope
        discovered = await self._discover(parent, resource_type, refresh=refresh)
        expected = f"{scope}/{name}" if resource_type != AE_TYPE else name
        if expected in discovered.resources:
            diagnostics = DiscoveryDiagnostics(
                status=DiscoveryStatus.DISCOVERED, scope=scope, resource_type=resource_type,
                requested_name=name, path=expected, source="discovery", verified=True,
                from_cache=discovered.diagnostics.from_cache,
            )
            return DiscoveryResult(expected, discovered.resources, diagnostics)

        fallback = self._validate_fallback(scope, name, resource_type, fallback_path)
        if fallback is not None:
            try:
                payload = await self.client.retrieve(fallback, operation_origin)
                if not isinstance(payload.get(_TYPE_KEYS[resource_type]), dict):
                    raise TinyIoTError("invalid_tinyiot_response", "Fallback has an unexpected resource type.", 502)
                diagnostics = DiscoveryDiagnostics(
                    status=DiscoveryStatus.FALLBACK, scope=scope, resource_type=resource_type,
                    requested_name=name, path=fallback, source="configured_fallback", verified=True,
                    fallback_attempted=True, error_code=discovered.diagnostics.error_code,
                    message="Discovery did not identify the resource; fallback was verified by retrieve.",
                )
                return DiscoveryResult(fallback, discovered.resources, diagnostics)
            except TinyIoTError as exc:
                if exc.code == "resource_not_found":
                    self.invalidate(ae_scope=scope, resource_type=resource_type)
                return self._result(scope, resource_type, name, None, self._status_for_error(exc),
                                    error_code=exc.code, message=str(exc),
                                    fallback_attempted=True, resources=discovered.resources)

        if discovered.resources:
            status = DiscoveryStatus.MISSING
            message = "The requested resource was not present in the discovery results."
        else:
            status = (DiscoveryStatus.MISSING
                      if discovered.diagnostics.status == DiscoveryStatus.DISCOVERED
                      else discovered.diagnostics.status)
            message = ("Discovery completed but returned no matching resources."
                       if status == DiscoveryStatus.MISSING else discovered.diagnostics.message)
        return self._result(scope, resource_type, name, None, status,
                            error_code=discovered.diagnostics.error_code, message=message,
                            fallback_attempted=fallback_path is not None,
                            resources=discovered.resources)

    def invalidate(self, *, ae_scope: str | None = None, resource_type: int | None = None) -> None:
        """Invalidate all or a scoped portion of the discovery cache."""
        if ae_scope is None and resource_type is None:
            self._cache.clear()
            return
        scope = self._validate_scope(ae_scope) if ae_scope is not None else None
        for key in list(self._cache):
            if scope is not None and key[0] != scope and not key[0].startswith(scope + "/"):
                continue
            if resource_type is not None and key[1] != resource_type:
                continue
            self._cache.pop(key, None)

    def cache_status(self) -> dict[str, Any]:
        """Return non-sensitive cache metadata for diagnostics."""
        now = self._clock()
        entries = []
        for (scope, resource_type), entry in self._cache.items():
            remaining = max(0.0, round(entry.expires_at - now, 3))
            if remaining == 0:
                continue
            entries.append({
                "scope": scope or "CSEBase",
                "resourceType": resource_type,
                "resourceCount": len(entry.resources),
                "ttlRemainingSeconds": remaining,
            })
        return {"ttlSeconds": self.ttl_seconds, "entryCount": len(entries), "entries": entries}

    async def refresh(self, *, ae_scope: str, resource_type: int,
                      container_name: str | None = None) -> DiscoveryResult:
        self.invalidate(ae_scope=ae_scope, resource_type=resource_type)
        if resource_type == SUBSCRIPTION_TYPE:
            if container_name is None:
                return self._invalid_result(ae_scope, resource_type)
            return await self.discover_subscriptions(ae_scope, container_name, refresh=True)
        if resource_type == CONTAINER_TYPE:
            return await self.discover_containers(ae_scope, refresh=True)
        return await self.discover_aes(refresh=True)

    async def _discover(self, scope: str, resource_type: int, *, refresh: bool) -> DiscoveryResult:
        cache_key = (scope, resource_type)
        async with self._lock:
            now = self._clock()
            if not refresh:
                entry = self._cache.get(cache_key)
                if entry is not None and entry.expires_at > now:
                    return self._result(scope, resource_type, None, None, DiscoveryStatus.DISCOVERED,
                                        resources=entry.resources, from_cache=True)
            try:
                resources = await self.client.discover(scope, self.discovery_origin,
                                                       resource_type=resource_type)
                normalized = self._normalize_all(resources, scope, resource_type)
                self._cache[cache_key] = _CacheEntry(now + self.ttl_seconds, tuple(normalized))
                return self._result(scope, resource_type, None, None, DiscoveryStatus.DISCOVERED,
                                    resources=tuple(normalized))
            except TinyIoTError as exc:
                return self._result(scope, resource_type, None, None, self._status_for_error(exc),
                                    error_code=exc.code, message=str(exc))
            except ValueError as exc:
                return self._result(scope, resource_type, None, None,
                                    DiscoveryStatus.INVALID_RESPONSE, error_code="invalid_uri",
                                    message=str(exc))

    def _normalize_all(self, resources: list[str], scope: str, resource_type: int) -> list[str]:
        if not isinstance(resources, list):
            raise ValueError("discovery resources must be a list")
        result = []
        seen = set()
        for raw in resources:
            path = self._normalize_uri(raw)
            if scope and not (path == scope or path.startswith(scope + "/")):
                raise ValueError("discovery URI is outside the requested scope")
            if resource_type == AE_TYPE and "/" in path:
                raise ValueError("AE discovery returned a non-AE descendant")
            if resource_type != AE_TYPE and path.count("/") != scope.count("/") + 1:
                raise ValueError("discovery URI has an unexpected depth")
            if path not in seen:
                seen.add(path)
                result.append(path)
        return result

    def _normalize_uri(self, raw: str) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("discovery URI must be a non-empty string")
        parsed = urlsplit(raw.strip())
        base = urlsplit(self.client.base_url)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise ValueError("external discovery URI rejected")
            value = parsed.path
        else:
            value = parsed.path
        if parsed.query or parsed.fragment:
            raise ValueError("discovery URI must not contain query or fragment")
        base_prefix = base.path.strip("/")
        value = value.strip("/")
        if value == base_prefix:
            value = ""
        elif value.startswith(base_prefix + "/"):
            value = value[len(base_prefix) + 1:]
        if not value or any(part in ("", ".", "..") for part in value.split("/")):
            raise ValueError("invalid or traversal discovery URI rejected")
        return value

    @staticmethod
    def _valid_name(name: str) -> bool:
        return isinstance(name, str) and bool(name) and "/" not in name and name not in (".", "..")

    def _validate_scope(self, scope: str | None) -> str | None:
        if not isinstance(scope, str) or not scope.strip():
            return None
        value = scope.strip("/")
        if any(part in ("", ".", "..") for part in value.split("/")):
            return None
        return value

    def _validate_fallback(self, scope: str, name: str, resource_type: int,
                           fallback_path: str | None) -> str | None:
        if not isinstance(fallback_path, str):
            return None
        try:
            path = self._normalize_uri(fallback_path)
        except ValueError:
            return None
        expected = f"{scope}/{name}" if resource_type != AE_TYPE else name
        return path if path == expected else None

    @staticmethod
    def _status_for_error(error: TinyIoTError) -> DiscoveryStatus:
        return {
            "tinyiot_forbidden": DiscoveryStatus.FORBIDDEN,
            "resource_not_found": DiscoveryStatus.MISSING,
            "tinyiot_unreachable": DiscoveryStatus.UNAVAILABLE,
            "invalid_tinyiot_response": DiscoveryStatus.INVALID_RESPONSE,
            "tinyiot_error": DiscoveryStatus.UNSUPPORTED,
        }.get(error.code, DiscoveryStatus.ERROR)

    def _invalid_result(self, scope: str, resource_type: int, name: str | None = None) -> DiscoveryResult:
        return self._result(scope, resource_type, name, None, DiscoveryStatus.INVALID_REQUEST,
                            error_code="invalid_request", message="Invalid discovery scope, name, or type.")

    @staticmethod
    def _result(scope: str, resource_type: int, name: str | None, path: str | None,
                status: DiscoveryStatus, *, resources: tuple[str, ...] = (), source: str | None = None,
                verified: bool = False, from_cache: bool = False, fallback_attempted: bool = False,
                error_code: str | None = None, message: str | None = None) -> DiscoveryResult:
        diagnostics = DiscoveryDiagnostics(status, scope, resource_type, name, path, source, verified,
                                           from_cache, fallback_attempted, error_code, message)
        return DiscoveryResult(path, tuple(resources), diagnostics)
