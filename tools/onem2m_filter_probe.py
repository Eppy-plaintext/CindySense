"""Isolated live TinyIoT notification-filter probe; never touches production SUBs."""

import json
import os
import threading
import time
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE = os.environ.get('CINDYSENSE_TINYIOT_URL', 'http://127.0.0.1:3001/TinyIoT').rstrip('/')
PARENT = 'CindySense_AE'
CALLBACK_HOST = os.environ.get('CINDYSENSE_WSL_HOST', '172.31.16.1')
CALLBACK_PORT = 8765


class Collector(BaseHTTPRequestHandler):
    events = []
    lock = threading.Lock()

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        try:
            signal = json.loads(data).get('m2m:sgn', {})
        except ValueError:
            signal = {}
        with self.lock:
            self.events.append({'sur': signal.get('sur'), 'vrq': bool(signal.get('vrq')),
                                'net': (signal.get('nev') or {}).get('net')})
        rqi = self.headers.get('X-M2M-RI', '')
        body = json.dumps({'m2m:rsp': {'rsc': 2000, 'rqi': rqi, 'rvi': '3'}}).encode()
        self.send_response(200)
        self.send_header('X-M2M-RSC', '2000')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def request(method, path, body=None, ty=None):
    headers = {'X-M2M-Origin': 'CAdmin', 'X-M2M-RVI': '4',
               'X-M2M-RI': 'filter-' + uuid.uuid4().hex, 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
    payload = json.dumps(body).encode() if body is not None else None
    req = Request(f'{BASE}/{path}', data=payload, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return response.status, response.headers.get('X-M2M-RSC'), json.loads(raw) if raw else {}


def counts(sub_ids):
    with Collector.lock:
        events = list(Collector.events)
    return {kind: sum(not event['vrq'] and event['sur'] == f'/tinyiot/{ri}'
                      for event in events) for kind, ri in sub_ids.items()}


def production_subscriptions():
    result = {}
    for name, path in [('RepCount_Sub', 'RepetitionCount'),
                       ('Summary_Sub', 'WorkoutSummary')]:
        status, rsc, data = request('GET', f'{PARENT}/{path}/{name}')
        if status != 200:
            raise RuntimeError(f'production SUB read failed: HTTP {status}, RSC {rsc}')
        sub = data['m2m:sub']
        result[name] = {'ri': sub['ri'], 'enc': sub['enc'], 'nu': sub['nu']}
    return result


def verify_filter_matrix(report):
    expected = {
        'cin-unlabeled': {'atr': 0, 'chty': 0},
        'cin-labeled': {'atr': 0, 'chty': 0},
        'cnt-unlabeled': {'atr': 0, 'chty': 1},
        'cnt-labeled': {'atr': 1, 'chty': 1},
    }
    actual = {entry['case']: entry['notifications'] for entry in report}
    if actual != expected or any(entry['http'] != 201 or entry['rsc'] != '2001'
                                 for entry in report):
        raise RuntimeError(f'TinyIoT filter behavior changed: {actual}')


def main():
    server = ThreadingHTTPServer(('0.0.0.0', CALLBACK_PORT), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    name = 'FilterProbe_' + uuid.uuid4().hex[:12]
    root = f'{PARENT}/{name}'
    created = False
    report = []
    try:
        production_before = production_subscriptions()
        status, rsc, _ = request('POST', PARENT, {'m2m:cnt': {'rn': name}}, 3)
        if status != 201:
            raise RuntimeError(f'test container create failed: HTTP {status}, RSC {rsc}')
        created = True
        sub_ids = {}
        for kind, enc in [('atr', {'net': [3], 'atr': ['lbl']}),
                          ('chty', {'net': [3], 'chty': [3]})]:
            status, rsc, data = request('POST', root, {'m2m:sub': {
                'rn': 'Filter_' + kind, 'nu': [f'http://{CALLBACK_HOST}:{CALLBACK_PORT}/probe'],
                'enc': enc, 'nct': 1}}, 23)
            if status != 201:
                raise RuntimeError(f'{kind} SUB create failed: HTTP {status}, RSC {rsc}')
            sub_ids[kind] = data.get('m2m:sub', {}).get('ri')
        if not all(sub_ids.values()):
            raise RuntimeError('SUB response omitted resource ID')
        time.sleep(0.5)
        cases = [
            ('cin-unlabeled', 4, {'m2m:cin': {'rn': 'cin_unlabeled', 'con': 'a'}}),
            ('cin-labeled', 4, {'m2m:cin': {'rn': 'cin_labeled', 'con': 'b', 'lbl': ['probe']}}),
            ('cnt-unlabeled', 3, {'m2m:cnt': {'rn': 'cnt_unlabeled'}}),
            ('cnt-labeled', 3, {'m2m:cnt': {'rn': 'cnt_labeled', 'lbl': ['probe']}}),
        ]
        for label, ty, body in cases:
            before = counts(sub_ids)
            status, rsc, _ = request('POST', root, body, ty)
            time.sleep(1)
            after = counts(sub_ids)
            report.append({'case': label, 'http': status, 'rsc': rsc,
                           'notifications': {kind: after[kind] - before[kind] for kind in sub_ids}})
        verify_filter_matrix(report)
        production_unchanged = production_before == production_subscriptions()
        if not production_unchanged:
            raise RuntimeError('production subscription changed during probe')
        print(json.dumps({'test_root': root, 'sub_ids_present': True, 'cases': report,
                          'total': counts(sub_ids), 'callback_ack_http': 200,
                          'callback_ack_rsc': '2000',
                          'production_subscriptions_unchanged': production_unchanged}, indent=2))
    finally:
        if created:
            status, rsc, _ = request('DELETE', root)
            absent, absent_rsc, _ = request('GET', root)
            print(json.dumps({'cleanup_http': status, 'cleanup_rsc': rsc,
                              'readback_http': absent, 'readback_rsc': absent_rsc}))
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
