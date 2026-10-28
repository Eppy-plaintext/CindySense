"""oneM2M client. Originators remain server-side; writes require API safeguards."""

import json
from uuid import uuid4

import httpx


class TinyIoTError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TinyIoTClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip('/')
        self._client = client

    async def retrieve(self, resource_path: str, origin: str) -> dict:
        return await self._request('GET', resource_path, origin)

    async def create_content_instance(self, resource_path: str, origin: str, resource_name: str, content: dict) -> dict:
        if not resource_name.startswith('sim-') or len(resource_name) != 36:
            raise TinyIoTError('invalid_resource_name', 'Simulation resource name is invalid.', 503)
        return await self._request('POST', resource_path, origin, {'m2m:cin': {'rn': resource_name, 'con': content}})

    async def _request(self, method: str, resource_path: str, origin: str, body: dict | None = None) -> dict:
        if not origin:
            raise TinyIoTError('origin_not_configured', 'The oneM2M originator is not configured.', 503)
        path = resource_path.strip('/')
        if (path and any(part in ('', '.', '..') for part in path.split('/'))) or '?' in path or '#' in path:
            raise TinyIoTError('invalid_resource_path', 'The configured oneM2M resource path is invalid.', 503)
        headers = {
            'X-M2M-Origin': origin,
            'X-M2M-RI': f'cindysense-{uuid4().hex}',
            'X-M2M-RVI': '4',
            'Accept': 'application/json',
        }
        if method == 'POST':
            headers['Content-Type'] = 'application/vnd.onem2m-res+json;ty=4'
        url = f'{self.base_url}/{path}' if path else self.base_url
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.request(method, url, headers=headers, json=body)
            else:
                response = await self._client.request(method, url, headers=headers, json=body)
        except httpx.RequestError as exc:
            raise TinyIoTError('tinyiot_unreachable', 'TinyIoT could not be reached.', 503) from exc

        rsc = response.headers.get('X-M2M-RSC')
        if response.status_code == 403 or rsc == '4103':
            raise TinyIoTError('tinyiot_forbidden', 'The configured oneM2M originator has no privilege for this resource.', 403)
        if response.status_code == 404 or rsc == '4004':
            raise TinyIoTError('resource_not_found', 'The configured oneM2M resource does not exist.', 404)
        if response.status_code == 409 or rsc == '4105':
            raise TinyIoTError('resource_conflict', 'A content instance with this event ID already exists.', 409)
        if response.is_error or (rsc and not rsc.startswith('2')):
            raise TinyIoTError('tinyiot_error', f'TinyIoT returned HTTP {response.status_code}.', 502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TinyIoTError('invalid_tinyiot_response', 'TinyIoT returned invalid JSON.', 502) from exc
        if not isinstance(payload, dict):
            raise TinyIoTError('invalid_tinyiot_response', 'TinyIoT returned an unexpected response shape.', 502)
        return payload

    async def latest_content(self, resource_path: str, origin: str) -> tuple[object, dict]:
        # Verify the configured container exists and is accessible before reading /la.
        container = await self.retrieve(resource_path, origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'The configured resource is not a oneM2M container.', 502)
        latest = await self.retrieve(f'{resource_path.strip("/")}/la', origin)
        return self.parse_content_instance(latest)

    @staticmethod
    def parse_content_instance(payload: dict) -> tuple[object, dict]:
        content_instance = payload.get('m2m:cin')
        if not isinstance(content_instance, dict) or 'con' not in content_instance:
            raise TinyIoTError('invalid_tinyiot_response', 'Latest content instance is missing content.', 502)
        content = content_instance['con']
        if isinstance(content, str):
            if not content.strip():
                raise TinyIoTError('invalid_content', 'Latest content instance is empty.', 502)
            try:
                content = json.loads(content)
            except ValueError:
                pass  # A plain-text recommendation may be valid; callers validate its shape.
        return content, content_instance
