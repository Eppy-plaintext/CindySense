"""Bounded oneM2M checks against an explicitly isolated TinyIoT instance.

This tool refuses the production port and creates only a UUID-named test AE.
"""

import argparse
import json
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port == 3001 or parsed.port is None or parsed.path != '/TinyIoT':
        parser.error('Use an explicit local isolated port and /TinyIoT path; production port 3001 is forbidden')
    records = []
    name = 'Phase6_' + uuid.uuid4().hex[:12]
    ae = name

    def request(method, path='', origin='CAdmin', body=None, ty=None, query=''):
        headers = {'X-M2M-Origin': origin, 'X-M2M-RVI': '4',
                   'X-M2M-RI': 'phase6-' + uuid.uuid4().hex, 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
        url = base + ('/' + path if path else '') + query
        req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, response.headers.get('X-M2M-RSC'), json.loads(raw) if raw else {}

    def check(label, method, path='', *, expected=(200, '2000'), **kwargs):
        status, rsc, body = request(method, path, **kwargs)
        records.append({'case': label, 'http': status, 'rsc': rsc})
        if (status, rsc) != expected:
            raise RuntimeError(f'{label}: HTTP {status}, RSC {rsc}; expected {expected}')
        return body

    check('initial root GET', 'GET')
    for index in range(10):
        check(f'sequential root GET {index + 1}', 'GET')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: request('GET'), range(24)))
    if any((status, rsc) != (200, '2000') for status, rsc, _ in results):
        raise RuntimeError('Concurrent GET returned an unexpected HTTP/RSC')
    records.append({'case': '24 GETs, maximum eight concurrent', 'count': len(results),
                    'http': 200, 'rsc': '2000'})

    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as sock:
        sock.settimeout(6)
        sock.sendall(b'GET /TinyIoT HTTP/1.1\r\nHost: localhost\r\n')
        raw = sock.recv(1024).decode('ascii', errors='replace')
    if not raw.startswith('HTTP/1.1 408') or 'X-M2M-RSC: 4008' not in raw:
        raise RuntimeError('Partial request did not return HTTP 408 / RSC 4008')
    records.append({'case': 'partial header receive timeout', 'http': 408, 'rsc': '4008'})
    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as sock:
        sock.sendall(b'GET /TinyIoT HTTP/1.1\r\nHost: localhost\r\nX-M2M-Origin: CAdmin\r\nX-M2M-RI: disconnected\r\nX-M2M-RVI: 4\r\n\r\n')
    time.sleep(0.3)
    check('root GET after client disconnect', 'GET')

    created = False
    try:
        check('test AE POST', 'POST', body={'m2m:ae': {'rn': ae, 'api': 'NPhase6Test',
                                                       'rr': False, 'srv': ['4']}},
              ty=2, expected=(201, '2001'))
        created = True
        check('test AE GET', 'GET', ae)
        policy = {'m2m:acp': {'rn': 'ScopedACP',
                             'pv': {'acr': [{'acor': ['CAdmin'], 'acop': 63},
                                            {'acor': ['CAllowed'], 'acop': 3}]},
                             'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}}}
        acp = check('ACP POST', 'POST', ae, body=policy, ty=1, expected=(201, '2001'))['m2m:acp']
        check('ACP GET', 'GET', ae + '/ScopedACP')
        for child in ('A', 'B'):
            check(f'CNT {child} POST', 'POST', ae,
                  body={'m2m:cnt': {'rn': child, 'lbl': ['phase6'] if child == 'A' else []}},
                  ty=3, expected=(201, '2001'))
        check('scoped ACP assignment PUT', 'PUT', ae + '/A',
              body={'m2m:cnt': {'acpi': [acp['ri']]}}, expected=(200, '2004'))
        a = check('scoped CNT GET', 'GET', ae + '/A', origin='CAllowed')['m2m:cnt']
        if a.get('acpi') != [acp['ri']]:
            raise RuntimeError('Stored ACP assignment mismatch')
        check('ACP denied GET', 'GET', ae + '/A', origin='CDenied', expected=(403, '4103'))
        check('ACP denied POST', 'POST', ae + '/A', origin='CDenied',
              body={'m2m:cin': {'rn': 'denied', 'con': 'denied'}}, ty=4, expected=(403, '4103'))
        check('CIN POST', 'POST', ae + '/A', origin='CAllowed',
              body={'m2m:cin': {'rn': 'item', 'con': 'phase6-isolated'}}, ty=4,
              expected=(201, '2001'))
        cin = check('CIN GET', 'GET', ae + '/A/item', origin='CAllowed')['m2m:cin']
        if cin.get('con') != 'phase6-isolated':
            raise RuntimeError('Stored CIN content mismatch')
        check('SUB POST', 'POST', ae + '/B',
              body={'m2m:sub': {'rn': 'TestSub', 'nu': ['CAdmin'],
                                'enc': {'net': [3]}, 'nct': 1}}, ty=23,
              expected=(201, '2001'))
        sub = check('SUB GET', 'GET', ae + '/B/TestSub')['m2m:sub']
        if sub.get('enc', {}).get('net') != [3]:
            raise RuntimeError('Stored subscription criteria mismatch')
        members = [f'TinyIoT/{ae}/A', f'TinyIoT/{ae}/B']
        check('GRP POST', 'POST', ae,
              body={'m2m:grp': {'rn': 'TestGroup', 'mt': 3, 'mid': members,
                                'mnm': 2, 'csy': 2}}, ty=9, expected=(201, '2001'))
        grp = check('GRP GET', 'GET', ae + '/TestGroup')['m2m:grp']
        if grp.get('mnm') != 2 or len(grp.get('mid', [])) != 2:
            raise RuntimeError('Stored group members mismatch')
        agr = check('GRP fopt GET', 'GET', ae + '/TestGroup/fopt').get('m2m:agr', {})
        responses = agr.get('m2m:rsp', [])
        if isinstance(responses, dict):
            responses = [responses]
        if len(responses) != 2 or any(str(item.get('rsc')) != '2000' for item in responses):
            raise RuntimeError('Fan-out member status mismatch')
        discovered = check('type discovery', 'GET', ae,
                           query='?fu=1&ty=3').get('m2m:uril', [])
        if len(discovered) < 2:
            raise RuntimeError('Type discovery missed test containers')
        labeled = check('label discovery', 'GET', ae,
                        query='?fu=1&ty=3&lbl=phase6').get('m2m:uril', [])
        if not any(str(ref).endswith('/A') for ref in labeled):
            raise RuntimeError('Label discovery missed test container')
    finally:
        if created:
            check('test AE cleanup DELETE', 'DELETE', ae, expected=(200, '2002'))
            check('test AE absent GET', 'GET', ae, expected=(404, '4004'))
    check('final root GET', 'GET')
    print(json.dumps({'status': 'passed', 'base': base, 'records': records}, indent=2))


if __name__ == '__main__':
    main()
