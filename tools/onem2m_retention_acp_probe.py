"""Disposable TinyIoT retention and ACP audit. Never mutates production resources."""

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = os.environ.get('CINDYSENSE_TINYIOT_URL', 'http://127.0.0.1:3001/TinyIoT').rstrip('/')
AE = 'CindySense_AE'
IDENTITIES = ('CAdmin', 'CDashboard', 'CCindyPT', 'CWebcam')
TARGETS = ('CindySense_AE/RepetitionCount', 'CindySense_AE/WorkoutSummary',
           'Webcam_AE/PoseData', 'CindyPT_AE/FitnessGoals',
           'CindyPT_AE/AIRecommendations', 'Dashboard_AE')


def request(method, path, origin='CAdmin', body=None, ty=None):
    headers = {'X-M2M-Origin': origin, 'X-M2M-RVI': '4',
               'X-M2M-RI': 'retention-' + uuid.uuid4().hex, 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
    req = Request(f'{BASE}/{path}', headers=headers, method=method,
                  data=json.dumps(body).encode() if body is not None else None)
    try:
        response = urlopen(req, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return {'http': response.status, 'rsc': response.headers.get('X-M2M-RSC'),
                'body': json.loads(raw) if raw else {}}


def compact(response):
    return {key: response[key] for key in ('http', 'rsc')}


def expect(response, status, operation):
    if response['http'] != status:
        raise RuntimeError(f'{operation}: HTTP {response["http"]}, RSC {response["rsc"]}')
    return response


def container(path):
    response = expect(request('GET', path), 200, f'GET {path}')
    return response['body']['m2m:cnt']


def cin(path, name, content):
    return compact(expect(request('POST', path, body={'m2m:cin': {'rn': name, 'con': content}},
                                  ty=4), 201, f'POST CIN {name}'))


def make_cnt(parent, name, **attributes):
    return compact(expect(request('POST', parent, body={'m2m:cnt': {'rn': name, **attributes}},
                                  ty=3), 201, f'POST CNT {name}'))


def update_cnt(path, **attributes):
    return compact(expect(request('PUT', path, body={'m2m:cnt': attributes}),
                          200, f'PUT CNT {path}'))


def retention(root):
    output = {}
    for name, attr, value in [('mni', 'mni', 2), ('mbs', 'mbs', 10), ('mia', 'mia', 2)]:
        parent = f'{root}/{name}'
        created = make_cnt(root, name, **{attr: value})
        create_value = container(parent).get(attr)
        updated = update_cnt(parent, **{attr: value})
        update_value = container(parent).get(attr)
        output[name] = {'create': created, 'create_value': create_value,
                        'update': updated, 'update_value': update_value}
        if update_value != value:
            raise RuntimeError(f'{attr} update not reflected; stopping experiment')
    path = f'{root}/mni'
    output['mni']['posts'] = [cin(path, f'item_{n}', str(n)) for n in (1, 2, 3)]
    output['mni']['readback'] = {f'item_{n}': compact(request('GET', f'{path}/item_{n}'))
                                 for n in (1, 2, 3)}
    output['mni']['cni'] = container(path).get('cni')

    path = f'{root}/mbs'
    output['mbs']['posts'] = [cin(path, f'item_{n}', chr(64 + n) * 6) for n in (1, 2, 3)]
    output['mbs']['readback'] = {f'item_{n}': compact(request('GET', f'{path}/item_{n}'))
                                 for n in (1, 2, 3)}
    data = container(path)
    output['mbs']['cni'], output['mbs']['cbs'] = data.get('cni'), data.get('cbs')

    path = f'{root}/mia'
    output['mia']['first_post'] = cin(path, 'old', 'old')
    time.sleep(3.2)
    output['mia']['before_new'] = compact(request('GET', f'{path}/old'))
    output['mia']['second_post'] = cin(path, 'new', 'new')
    output['mia']['after_new'] = {name: compact(request('GET', f'{path}/{name}'))
                                  for name in ('old', 'new')}
    output['mia']['cni'] = container(path).get('cni')

    expiry = (datetime.now(timezone.utc) + timedelta(seconds=4)).strftime('%Y%m%dT%H%M%S,%f')[:19]
    output['et'] = {'create': make_cnt(root, 'expires', et=expiry), 'et': expiry}
    path = f'{root}/expires'
    output['et']['before'] = compact(request('GET', path))
    time.sleep(5)
    output['et']['after'] = compact(request('GET', path))
    return output


def verify_retention_results(results):
    expected = {
        'mni': {'item_1': 404, 'item_2': 200, 'item_3': 200},
        'mbs': {'item_1': 404, 'item_2': 404, 'item_3': 200},
    }
    for kind, statuses in expected.items():
        observed = {name: item['http'] for name, item in results[kind]['readback'].items()}
        if observed != statuses or results[kind]['create_value'] != (2 if kind == 'mni' else 10):
            raise RuntimeError(f'{kind} retention differs from the verified behavior: {observed}')
    if (results['mni']['cni'] != 2 or results['mbs']['cni'] != 1 or
            results['mbs']['cbs'] != 6 or results['mia']['before_new']['http'] != 200 or
            results['mia']['after_new']['old']['http'] != 404 or
            results['mia']['after_new']['new']['http'] != 200 or
            results['mia']['cni'] != 1 or results['et']['before']['http'] != 200 or
            results['et']['after']['http'] != 404):
        raise RuntimeError('age or expiration retention differs from the verified behavior')


def acp_audit(root):
    acps = {}
    for name in ('defaultACP', 'Dashboard_ACP', 'CindyPT_ACP', 'CindySense_ACP'):
        response = request('GET', name)
        acps[name] = {'status': compact(response),
                      'pv': response['body'].get('m2m:acp', {}).get('pv', {}).get('acr', []),
                      'pvs': response['body'].get('m2m:acp', {}).get('pvs', {}).get('acr', [])}
    targets = {}
    for path in TARGETS:
        reads = {origin: compact(request('GET', path, origin)) for origin in IDENTITIES}
        admin = request('GET', path)
        obj = next(iter(admin['body'].values()), {})
        targets[path] = {'acpi': obj.get('acpi'), 'reads': reads}
    writes = {}
    for origin in IDENTITIES:
        create_name = 'create_' + origin
        result = request('POST', root, origin, {'m2m:cnt': {'rn': create_name}}, 3)
        writes[origin] = {'create': compact(result)}
        if result['http'] not in (201, 403):
            raise RuntimeError(f'unexpected CREATE status for {origin}: {compact(result)}')
        result = request('PUT', root, origin, {'m2m:cnt': {'lbl': [origin]}})
        writes[origin]['update'] = compact(result)
        if result['http'] not in (200, 403):
            raise RuntimeError(f'unexpected UPDATE status for {origin}: {compact(result)}')
        delete_path = f'{root}/delete_{origin}'
        make_cnt(root, 'delete_' + origin)
        result = request('DELETE', delete_path, origin)
        writes[origin]['delete'] = compact(result)
        if result['http'] not in (200, 403):
            raise RuntimeError(f'unexpected DELETE status for {origin}: {compact(result)}')
    return {'acps': acps, 'targets': targets, 'disposable_writes': writes}


def main():
    name = 'RetentionAudit_' + uuid.uuid4().hex[:12]
    root = f'{AE}/{name}'
    created = False
    try:
        make_cnt(AE, name)
        created = True
        results = retention(root)
        verify_retention_results(results)
        print(json.dumps({'retention': results, 'acp': acp_audit(root)}, indent=2))
    finally:
        if created:
            removed = compact(request('DELETE', root))
            absent = compact(request('GET', root))
            print(json.dumps({'test_root': root, 'cleanup': removed, 'readback': absent}))


if __name__ == '__main__':
    main()
