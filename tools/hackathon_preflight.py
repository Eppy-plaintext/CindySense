"""Read-only oneM2M preflight; prints sanitized resource metadata only."""

import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE = os.getenv('TINYIOT_BASE_URL', 'http://127.0.0.1:3001/TinyIoT').rstrip('/')
ORIGIN = os.getenv('TINYIOT_AUDIT_ORIGIN', '')


def get(path='', query=None):
    if not ORIGIN:
        raise RuntimeError('Set TINYIOT_AUDIT_ORIGIN locally before read-only preflight.')
    url = BASE + ('/' + path.strip('/') if path else '')
    if query:
        url += '?' + urlencode(query)
    request = Request(url, headers={
        'X-M2M-Origin': ORIGIN, 'X-M2M-RI': 'audit-' + uuid.uuid4().hex,
        'X-M2M-RVI': '4', 'Accept': 'application/json'})
    try:
        response = urlopen(request, timeout=4)
    except HTTPError as exc:
        response = exc
    with response:
        try:
            payload = json.load(response)
        except ValueError:
            payload = {}
        return response.status, response.headers.get('X-M2M-RSC'), payload


def main():
    status, rsc, root = get()
    if (status, rsc) != (200, '2000') or 'm2m:cb' not in root:
        raise RuntimeError(f'CSE preflight failed: HTTP {status}, RSC {rsc}')
    status, rsc, discovered = get(query={'fu': 1, 'rcn': 11})
    if (status, rsc) != (200, '2000') or not isinstance(discovered.get('m2m:uril'), list):
        raise RuntimeError(f'Root discovery failed: HTTP {status}, RSC {rsc}')
    resources = []
    for uri in discovered['m2m:uril']:
        if not isinstance(uri, str) or not uri.startswith('TinyIoT/'):
            raise RuntimeError('Unexpected discovery URI')
        path = uri.removeprefix('TinyIoT/')
        status, rsc, data = get(path)
        if (status, rsc) != (200, '2000') or len(data) != 1:
            resources.append({'path': path, 'http': status, 'rsc': rsc})
            continue
        kind, item = next(iter(data.items()))
        if not isinstance(item, dict):
            raise RuntimeError('Malformed resource representation')
        resources.append({'path': path, 'kind': kind, 'ri': item.get('ri'),
                          'ty': item.get('ty'), 'pi': item.get('pi'),
                          'cni': item.get('cni'), 'ch': item.get('ch'),
                          'aei': item.get('aei'), 'acpi': item.get('acpi'),
                          'mid': item.get('mid')})
    print(json.dumps({'cse': 'TinyIoT', 'resource_count': len(resources),
                      'resources': resources}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, URLError, TimeoutError, OSError) as exc:
        # Never print the base URL, notification URI, token, or originator.
        print(f'Preflight unavailable: {type(exc).__name__}')
        raise SystemExit(1)
