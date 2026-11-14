"""oneM2M client. Originators remain server-side; writes require API safeguards."""

import json
import hashlib
import logging
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

log = logging.getLogger(__name__)


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

    async def discover(self, resource_path: str, origin: str, resource_type: int | None = None,
                       label: str | None = None) -> list[str]:
        """Return authorized descendant URIs through TinyIoT discovery."""
        params = {'fu': 1, 'rcn': 11}
        if resource_type is not None:
            params['ty'] = resource_type
        if label is not None:
            params['lbl'] = label
        payload = await self._request('GET', resource_path, origin, params=params)
        references = payload.get('m2m:uril')
        if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
            raise TinyIoTError('invalid_tinyiot_response', 'Discovery response is missing m2m:uril.', 502)
        return references

    async def create_subscription(self, resource_path: str, origin: str, name: str, notification_uri: str) -> dict:
        return await self._request('POST', resource_path, origin, {
            'm2m:sub': {'rn': name, 'nu': [notification_uri], 'enc': {'net': [3]}, 'nct': 1},
        }, content_type='application/json;ty=23')

    async def create_group(self, parent_path: str, origin: str, name: str,
                           member_paths: list[str], member_type: int = 3,
                           consistency: int = 2) -> dict:
        """Create a typed oneM2M GRP with existing member references."""
        if not name or '/' in name or name in ('.', '..'):
            raise TinyIoTError('invalid_resource_name', 'Group resource name is invalid.', 503)
        base_prefix = urlsplit(self.base_url).path.strip('/')
        members = [path.strip('/') for path in member_paths]
        # TinyIoT expects SP-relative member references during GRP validation.
        # Keep the public helper convenient by accepting repository-style paths.
        members = [path if path == base_prefix or path.startswith(base_prefix + '/')
                   else f'{base_prefix}/{path}' for path in members]
        if not members or any(not path or any(part in ('', '.', '..') for part in path.split('/'))
                              for path in members):
            raise TinyIoTError('invalid_group_members', 'Group member paths are invalid.', 503)
        return await self._request('POST', parent_path, origin, {
            'm2m:grp': {'rn': name, 'mt': member_type, 'mid': members,
                        'mnm': len(members), 'csy': consistency},
        }, content_type='application/json;ty=9')

    async def group_members(self, group_path: str, origin: str) -> list[str]:
        payload = await self.retrieve(group_path, origin)
        group = payload.get('m2m:grp')
        members = group.get('mid') if isinstance(group, dict) else None
        if isinstance(group, dict) and (group.get('mt', 3) != 3 or
                                         group.get('mnm', len(members) if isinstance(members, list) else 0)
                                         != (len(members) if isinstance(members, list) else 0)):
            raise TinyIoTError('invalid_tinyiot_response', 'Group member type or count is invalid.', 502)
        if not isinstance(members, list) or any(not isinstance(item, str) for item in members):
            raise TinyIoTError('invalid_tinyiot_response', 'Group representation is missing mid.', 502)
        return members

    async def fanout_retrieve(self, group_path: str, origin: str) -> dict:
        """Retrieve group members through TinyIoT's fanout point (read-only)."""
        return await self.retrieve(f'{group_path.strip('/')}/fopt', origin)

    async def update_subscription(self, resource_path: str, origin: str, notification_uri: str) -> dict:
        return await self._request('PUT', resource_path, origin, {
            'm2m:sub': {'nu': [notification_uri], 'enc': {'net': [3]}, 'nct': 1},
        }, content_type='application/json')

    async def embedded_subscriptions(self, resource_path: str, origin: str) -> list[dict]:
        payload = await self._request('GET', resource_path, origin, params={'rcn': 4})
        container = payload.get('m2m:cnt')
        if not isinstance(container, dict):
            raise TinyIoTError('invalid_tinyiot_response', 'Container child representation is malformed.', 502)
        children = container.get('m2m:sub', [])
        if isinstance(children, dict):
            children = [children]
        if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
            raise TinyIoTError('invalid_tinyiot_response', 'Embedded subscriptions are malformed.', 502)
        return children

    async def create_content_instance(self, resource_path: str, origin: str, resource_name: str, content: dict) -> dict:
        if not resource_name.startswith('sim-') or len(resource_name) != 36:
            raise TinyIoTError('invalid_resource_name', 'Simulation resource name is invalid.', 503)
        return await self._request('POST', resource_path, origin, {'m2m:cin': {'rn': resource_name, 'con': content}})

    async def create_content(self, resource_path: str, origin: str, content: dict) -> dict:
        """Append a server-named CIN to an existing container."""
        container = await self.retrieve(resource_path, origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'The configured resource is not a oneM2M container.', 502)
        return await self._request('POST', resource_path, origin, {
            'm2m:cin': {'cnf': 'application/json:0', 'con': json.dumps(content, separators=(',', ':'))},
        }, content_type='application/json;ty=4')

    async def create_named_content(self, resource_path: str, origin: str,
                                   resource_name: str, content: dict) -> bool:
        """Create a plan CIN once; verify exact content after retries or lost replies."""
        if not resource_name.startswith('plan-') or len(resource_name) != 69:
            raise TinyIoTError('invalid_resource_name', 'Plan resource name is invalid.', 503)
        container = await self.retrieve(resource_path, origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'The configured resource is not a oneM2M container.', 502)
        child = f'{resource_path.strip("/")}/{resource_name}'

        async def verify() -> bool:
            try:
                stored = await self.retrieve(child, origin)
            except TinyIoTError as exc:
                if exc.code == 'resource_not_found':
                    return False
                raise
            parsed, instance = self.parse_content_instance(stored)
            if instance.get('rn', resource_name) != resource_name or parsed != content:
                raise TinyIoTError('plan_conflict', 'A saved plan ID has different content.', 409)
            return True

        if await verify():
            return False
        body = {'m2m:cin': {'rn': resource_name, 'cnf': 'application/json:0',
                            'con': json.dumps(content, separators=(',', ':'))}}
        try:
            await self._request('POST', resource_path, origin, body, content_type='application/json;ty=4')
        except TinyIoTError as exc:
            if exc.code not in ('resource_conflict', 'tinyiot_error', 'tinyiot_unreachable') or not await verify():
                raise
            return False
        if not await verify():
            raise TinyIoTError('plan_unconfirmed', 'Plan write succeeded but readback was unavailable.', 502)
        return True

    async def create_named_verified_content(self, resource_path: str, origin: str,
                                            resource_name: str, content: dict) -> bool:
        """Create a deterministic named CIN and verify exact content after retries."""
        if not resource_name or '/' in resource_name or resource_name in ('.', '..'):
            raise TinyIoTError('invalid_resource_name', 'Content instance name is invalid.', 503)
        container = await self.retrieve(resource_path, origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'The configured resource is not a oneM2M container.', 502)
        child = f'{resource_path.strip("/")}/{resource_name}'

        async def verify() -> bool:
            try:
                stored = await self.retrieve(child, origin)
            except TinyIoTError as exc:
                if exc.code == 'resource_not_found':
                    return False
                raise
            parsed, instance = self.parse_content_instance(stored)
            if instance.get('rn', resource_name) != resource_name or parsed != content:
                raise TinyIoTError('content_conflict', 'Named content has different content.', 409)
            return True

        if await verify():
            return False
        body = {'m2m:cin': {'rn': resource_name, 'cnf': 'application/json:0',
                           'con': json.dumps(content, separators=(',', ':'))}}
        try:
            await self._request('POST', resource_path, origin, body,
                                content_type='application/json;ty=4')
        except TinyIoTError as exc:
            if exc.code not in ('resource_conflict', 'tinyiot_error', 'tinyiot_unreachable') or not await verify():
                raise
            return False
        if not await verify():
            raise TinyIoTError('content_unconfirmed', 'Named content could not be read back.', 502)
        return True

    async def create_named_recommendation(self, resource_path: str, origin: str,
                                          idempotency_key: str, content: dict) -> bool:
        """Create a deterministic CIN and verify it after a lost reply or conflict."""
        name = 'coach-' + hashlib.sha256(idempotency_key.encode()).hexdigest()
        container = await self.retrieve(resource_path, origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'Recommendation resource is not a container.', 502)
        child = f'{resource_path.strip("/")}/{name}'

        async def verify():
            try:
                stored = await self.retrieve(child, origin)
            except TinyIoTError as exc:
                if exc.code == 'resource_not_found':
                    return False
                raise
            parsed, instance = self.parse_content_instance(stored)
            if instance.get('rn', name) != name or parsed != content:
                raise TinyIoTError('recommendation_conflict', 'Recommendation ID has different content.', 409)
            return True

        if await verify():
            return False
        body = {'m2m:cin': {'rn': name, 'cnf': 'application/json:0',
                           'con': json.dumps(content, separators=(',', ':'))}}
        try:
            await self._request('POST', resource_path, origin, body,
                                content_type='application/json;ty=4')
        except TinyIoTError as exc:
            if exc.code not in ('resource_conflict', 'tinyiot_error', 'tinyiot_unreachable') or not await verify():
                raise
            return False
        if not await verify():
            raise TinyIoTError('recommendation_unconfirmed', 'Recommendation write could not be read back.', 502)
        return True

    async def _request(self, method: str, resource_path: str, origin: str, body: dict | None = None,
                       params: dict | None = None, content_type: str | None = None) -> dict:
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
        if method in ('POST', 'PUT'):
            headers['Content-Type'] = content_type or 'application/vnd.onem2m-res+json;ty=4'
        url = f'{self.base_url}/{path}' if path else self.base_url
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.request(method, url, headers=headers, json=body, params=params)
            else:
                response = await self._client.request(method, url, headers=headers, json=body, params=params)
        except httpx.RequestError as exc:
            raise TinyIoTError('tinyiot_unreachable', 'TinyIoT could not be reached.', 503) from exc

        rsc = response.headers.get('X-M2M-RSC')
        if response.is_error or (rsc and not rsc.startswith('2')):
            # Never log the request body or notification URI: it carries a token.
            log.warning('oneM2M request failed method=%s resource=%s http=%s rsc=%s',
                        method, path, response.status_code, rsc or 'absent')
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

    async def list_content_instances(self, resource_path: str, origin: str, page_size: int = 100,
                                     max_pages: int = 21, stop_at: set[str] | None = None,
                                     expected_count: int | None = None) -> list[str]:
        """List newest CINs until a previously seen reference or the retained end."""
        container = resource_path.strip('/')
        refs: list[str] = []
        seen: set[str] = set()
        for page in range(max_pages):
            payload = await self._request('GET', container, origin, params={
                'rcn': 6, 'ty': 4, 'lvl': 1, 'lim': page_size, 'ofst': page * page_size,
            })
            rrl = payload.get('m2m:rrl')
            entries = rrl.get('rrf') if isinstance(rrl, dict) else None
            if not isinstance(entries, list):
                raise TinyIoTError('invalid_tinyiot_response', 'CIN listing is missing m2m:rrl.rrf.', 502)
            for entry in entries:
                if not isinstance(entry, dict) or entry.get('typ') != 4:
                    raise TinyIoTError('invalid_tinyiot_response', 'CIN listing contains an invalid reference.', 502)
                reference = entry.get('val')
                if not isinstance(reference, str):
                    raise TinyIoTError('invalid_tinyiot_response', 'CIN listing contains an invalid path.', 502)
                parsed = urlsplit(reference)
                base = urlsplit(self.base_url)
                if (parsed.query or parsed.fragment or (parsed.scheme and not parsed.netloc) or
                        (parsed.netloc and (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc))):
                    raise TinyIoTError('invalid_tinyiot_response', 'CIN listing contains an external or invalid path.', 502)
                path = parsed.path.strip('/')
                base_prefix = base.path.strip('/')
                if path.startswith(base_prefix + '/'):
                    path = path[len(base_prefix) + 1:]
                if '/' not in path:
                    path = f'{container}/{path}'
                if not path.startswith(container + '/') or any(part in ('', '.', '..') for part in path.split('/')):
                    raise TinyIoTError('invalid_tinyiot_response', 'CIN listing points outside the repetition container.', 502)
                if path not in seen:
                    seen.add(path)
                    refs.append(path)
                if stop_at is not None and path in stop_at:
                    return refs
            if (len(entries) < page_size or
                    expected_count is not None and len(refs) >= expected_count):
                return refs
        raise TinyIoTError('listing_limit', 'Too many retained repetition events for the configured listing limit.', 503)

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
