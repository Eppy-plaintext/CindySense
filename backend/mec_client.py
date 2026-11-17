"""Read-only ETSI MEC 028 WLAN client.

This module is intentionally independent from the workout and oneM2M flows.
It performs no oneM2M writes and does not publish MEC data anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import httpx


class MecErrorCode(StrEnum):
    DISABLED = 'mec_disabled'
    INVALID_CONFIGURATION = 'invalid_configuration'
    HTTP_ERROR = 'http_error'
    TIMEOUT = 'timeout'
    CONNECTION_ERROR = 'connection_error'
    INVALID_CONTENT_TYPE = 'invalid_content_type'
    INVALID_JSON = 'invalid_json'
    INVALID_RESPONSE = 'invalid_response'


@dataclass(frozen=True)
class MecWlanConfig:
    enabled: bool = False
    api_base_url: str = ''
    timeout_seconds: float = 10.0

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise MecClientError(MecErrorCode.INVALID_CONFIGURATION,
                                 'MEC request timeout must be positive.', 503)
        if not self.enabled:
            return
        parsed = urlsplit(self.api_base_url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
            raise MecClientError(
                MecErrorCode.INVALID_CONFIGURATION,
                'An enabled MEC client requires an HTTPS URL without query or fragment.', 503)


class MecClientError(Exception):
    """Sanitized, structured MEC client failure."""

    def __init__(self, code: MecErrorCode | str, message: str, status_code: int,
                 *, retryable: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {'code': self.code, 'message': self.message,
                'status_code': self.status_code, 'retryable': self.retryable}


class MecWlanClient:
    """Fetch MEC 028 AP information over verified HTTPS."""

    AP_INFORMATION_PATH = '/queries/ap/ap_information'

    def __init__(self, config: MecWlanConfig, client: httpx.AsyncClient | None = None):
        config.validate()
        self.config = config
        self._client = client

    def _access_point_url(self) -> str:
        """Build the AP-information URL from a configured MEC API base."""
        parsed = urlsplit(self.config.api_base_url)
        path = parsed.path.rstrip('/')
        if not path.endswith(self.AP_INFORMATION_PATH):
            path += self.AP_INFORMATION_PATH
        return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))

    async def fetch_access_points(self) -> list[dict[str, Any]]:
        if not self.config.enabled:
            raise MecClientError(MecErrorCode.DISABLED,
                                 'MEC WLAN access is disabled.', 503)

        request_url = self._access_point_url()

        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds,
                                             verify=True, follow_redirects=False) as client:
                    response = await client.get(request_url,
                                                headers={'Accept': 'application/json'})
            else:
                response = await self._client.get(
                    request_url, headers={'Accept': 'application/json'})
        except httpx.TimeoutException as exc:
            raise MecClientError(MecErrorCode.TIMEOUT,
                                 'The MEC WLAN request timed out.', 504,
                                 retryable=True) from exc
        except httpx.RequestError as exc:
            raise MecClientError(MecErrorCode.CONNECTION_ERROR,
                                 'The MEC WLAN service could not be reached.', 503,
                                 retryable=True) from exc

        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code >= 500 or response.status_code == 429
            raise MecClientError(MecErrorCode.HTTP_ERROR,
                                 f'MEC WLAN returned HTTP {response.status_code}.',
                                 response.status_code, retryable=retryable)

        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            raise MecClientError(MecErrorCode.INVALID_CONTENT_TYPE,
                                 'MEC WLAN returned a non-JSON response.', 502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MecClientError(MecErrorCode.INVALID_JSON,
                                 'MEC WLAN returned malformed JSON.', 502) from exc
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise MecClientError(MecErrorCode.INVALID_RESPONSE,
                                 'MEC WLAN returned an unexpected access-point list.', 502)
        return payload

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
