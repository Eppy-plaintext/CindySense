"""Bounded Cindy-like cadence against the isolated TinyIoT staging instance.

One disposable CIN per second models the webcam's 1 s pose publish interval;
one container GET per two seconds models the dashboard's 2 s snapshot poll.
The script refuses every port except the isolated Phase 6/7 port 3101.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import statistics
import subprocess
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--expected-server-exe',
                        help='Absolute isolated server executable; required when production port is active')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1200:
        parser.error('duration must be 1..1200 seconds')
    base = 'http://127.0.0.1:3101/TinyIoT'
    ae = 'Phase7Endurance_' + uuid.uuid4().hex[:10]
    results = {'duration_requested_seconds': args.seconds, 'ae': ae,
               'write': {'ok': 0, 'failed': 0}, 'read': {'ok': 0, 'failed': 0},
               'discovery_cin_pair': {'ok': 0, 'failed': 0},
               'samples': [], 'status': 'running'}
    latencies = []

    def request(method, path='', origin='CAdmin', body=None, ty=None):
        headers = {'X-M2M-Origin': origin, 'X-M2M-RVI': '4',
                   'X-M2M-RI': 'phase7-endurance-' + uuid.uuid4().hex}
        if body is not None:
            headers['Content-Type'] = 'application/json' + (f';ty={ty}' if ty else '')
        req = Request(base + ('/' + path if path else ''), method=method,
                      headers=headers, data=json.dumps(body).encode() if body is not None else None)
        start = time.monotonic()
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            response.read()
            status = response.status, response.headers.get('X-M2M-RSC')
        latencies.append((time.monotonic() - start) * 1000)
        return status

    def metrics():
        ss = subprocess.run(['wsl.exe', '-e', 'ss', '-ltnp'], capture_output=True, text=True).stdout
        match = re.search(r':3101\s+.*pid=(\d+)', ss)
        if not match:
            raise RuntimeError('isolated server missing')
        pid = match.group(1)
        if re.search(r':3001\s', ss):
            if not args.expected_server_exe:
                raise RuntimeError('production port active; isolated executable not verified')
            actual_exe = subprocess.run(['wsl.exe', '-e', 'readlink', '-f',
                                         f'/proc/{pid}/exe'], capture_output=True,
                                        text=True, check=True).stdout.strip()
            if actual_exe != args.expected_server_exe:
                raise RuntimeError(f'unexpected isolated executable: {actual_exe}')
        def count(path):
            output = subprocess.run(['wsl.exe', '-e', 'ls', path],
                                    capture_output=True, text=True, check=True).stdout
            return len(output.splitlines())
        status = subprocess.run(['wsl.exe', '-e', 'cat', f'/proc/{pid}/status'],
                                capture_output=True, text=True, check=True).stdout
        rss = int(re.search(r'^VmRSS:\s+(\d+)', status, re.M).group(1))
        sockets = subprocess.run(['wsl.exe', '-e', 'ss', '-tan', 'state', 'close-wait'],
                                 capture_output=True, text=True).stdout
        pg = subprocess.run(['wsl.exe', '-e', '/usr/lib/postgresql/14/bin/pg_isready',
                             '-h', '127.0.0.1', '-p', '55432'],
                            capture_output=True, text=True)
        return {'threads': count(f'/proc/{pid}/task'), 'fds': count(f'/proc/{pid}/fd'),
                'close_wait': sum(':3101' in line for line in sockets.splitlines()),
                'rss_kib': rss, 'postgres_ready': pg.returncode == 0}

    assert request('GET') == (200, '2000')
    assert request('POST', body={'m2m:ae': {'rn': ae, 'api': 'NPhase7Endurance',
                                           'rr': False, 'srv': ['4']}}, ty=2) == (201, '2001')
    try:
        assert request('POST', ae, body={'m2m:cnt': {'rn': 'PoseSamples'}}, ty=3) == (201, '2001')
        policy = {'m2m:acp': {'rn': 'PublisherPolicy',
                              'pv': {'acr': [{'acor': ['CAdmin'], 'acop': 63},
                                             {'acor': ['CWebcam'], 'acop': 3}]},
                              'pvs': {'acr': [{'acor': ['CAdmin'], 'acop': 63}]}}}
        assert request('POST', ae, body=policy, ty=1) == (201, '2001')
        headers = {'X-M2M-Origin': 'CAdmin', 'X-M2M-RVI': '4', 'X-M2M-RI': uuid.uuid4().hex}
        with urlopen(Request(base + '/' + ae + '/PublisherPolicy', headers=headers), timeout=5) as response:
            acp_ri = json.load(response)['m2m:acp']['ri']
        assert request('PUT', ae + '/PoseSamples',
                       body={'m2m:cnt': {'acpi': [acp_ri]}}) == (200, '2004')
        baseline = metrics()
        results['baseline'] = baseline
        start = time.monotonic()
        next_write = start
        next_read = start
        next_sample = start + 60
        index = 0
        with ThreadPoolExecutor(max_workers=2) as pair_pool:
          while time.monotonic() - start < args.seconds:
            now = time.monotonic()
            if now >= next_write:
                index += 1
                try:
                    status = request('POST', ae + '/PoseSamples', origin='CWebcam',
                                     body={'m2m:cin': {'rn': f'sample_{index:05}',
                                                        'con': 'phase7-isolated-pose'}}, ty=4)
                    results['write']['ok' if status == (201, '2001') else 'failed'] += 1
                except Exception:
                    results['write']['failed'] += 1
                next_write += 1.0
            if now >= next_read:
                try:
                    status = request('GET', ae + '/PoseSamples', origin='CWebcam')
                    results['read']['ok' if status == (200, '2000') else 'failed'] += 1
                except Exception:
                    results['read']['failed'] += 1
                if index:
                    try:
                        paths = [ae + '/PoseSamples?fu=1',
                                 ae + f'/PoseSamples/sample_{index:05}']
                        statuses = list(pair_pool.map(lambda path: request('GET', path), paths))
                        key = 'ok' if all(status == (200, '2000') for status in statuses) else 'failed'
                        results['discovery_cin_pair'][key] += 1
                    except Exception:
                        results['discovery_cin_pair']['failed'] += 1
                next_read += 2.0
            if now >= next_sample:
                sample = metrics()
                sample['elapsed_seconds'] = round(now - start, 1)
                results['samples'].append(sample)
                print(json.dumps({'sample': sample}), flush=True)
                if (sample['threads'] > 16 or sample['fds'] > 32 or sample['close_wait'] > 8
                        or sample['rss_kib'] > baseline['rss_kib'] + 65536
                        or not sample['postgres_ready']):
                    results['status'] = 'stopped_on_resource_threshold'
                    break
                next_sample += 60
            time.sleep(0.05)
          else:
            results['status'] = 'completed'
        results['elapsed_seconds'] = round(time.monotonic() - start, 1)
        results['final_metrics'] = metrics()
        results['latency_ms'] = {'count': len(latencies),
                                 'median': round(statistics.median(latencies), 2),
                                 'p95': round(sorted(latencies)[int(len(latencies) * .95)], 2),
                                 'maximum': round(max(latencies), 2)}
    finally:
        results['cleanup_delete'] = request('DELETE', ae)
        results['cleanup_absent'] = request('GET', ae)
        print(json.dumps({'result': results}), flush=True)
    if (results['status'] != 'completed' or results['write']['failed']
            or results['read']['failed'] or results['discovery_cin_pair']['failed']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
