"""Read-only Cindy reliability observer: TinyIoT CIN IDs, FastAPI metrics, SSE."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import time
from urllib.parse import urlsplit

import httpx


REPETITIONS = 'CindySense_AE/RepetitionCount'
LOG_DETECTED = re.compile(r'^\[REP\] detectedAt=\S+ id=([0-9a-fA-F-]{36})\b')
LOG_CONFIRMED = re.compile(r'^\[TinyIoT\] Event confirmed at=\S+ id=([0-9a-fA-F-]{36})\b')


def snapshot_count(payload):
    source = (payload.get('sources') or {}).get('webcam') or {}
    return source.get('sessionId'), source.get('eventCount')


def utc_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def latency_ms(start, end):
    first, last = utc_time(start), utc_time(end)
    if first is None or last is None:
        return None
    delta = (last - first).total_seconds() * 1000
    return round(delta) if 0 <= delta <= 3600000 else None


def duration_stats(values):
    if not values:
        return {'count': 0, 'minMs': None, 'medianMs': None, 'p95Ms': None, 'maxMs': None}
    ordered = sorted(values)
    return {'count': len(values), 'minMs': ordered[0], 'medianMs': round(statistics.median(ordered)),
            'p95Ms': ordered[(95 * len(ordered) + 99) // 100 - 1], 'maxMs': ordered[-1]}


class EventLedger:
    def __init__(self):
        self.stored = {}
        self.sse = {}
        self.baseline_refs = set()
        self.seen_refs = set()
        self.last_sequence = {}
        self.out_of_order = []
        self.reconnects = 0
        self.reconcile_events = 0
        self.bad_sse_payloads = 0
        self.repetition_signals = 0
        self.summary_signals = 0
        self.latencies = {'eventToCallbackMs': [], 'callbackToSseMs': [],
                          'sseToObserverMs': [], 'eventToObserverMs': []}

    def baseline(self, refs):
        self.baseline_refs = set(refs)
        self.seen_refs.update(refs)

    def store(self, ref, content):
        self.seen_refs.add(ref)
        if not isinstance(content, dict) or content.get('source') != 'webcam':
            return
        event_id = content.get('eventId')
        if not isinstance(event_id, str) or not event_id:
            return
        self.stored[event_id] = {'ref': ref, 'sessionId': content.get('sessionId'),
                                 'eventSequence': content.get('eventSequence'),
                                 'timestamp': content.get('timestamp')}

    def observe(self, kind, data, observed_at=None):
        if kind == 'reconcile':
            self.reconcile_events += 1
            return
        if kind == 'summary':
            self.summary_signals += 1
            return
        if kind != 'repetition':
            return
        self.repetition_signals += 1
        if not isinstance(data, dict) or not isinstance(data.get('eventId'), str):
            self.bad_sse_payloads += 1
            return
        event_id = data['eventId']
        self.sse[event_id] = self.sse.get(event_id, 0) + 1
        session, sequence = data.get('sessionId'), data.get('eventSequence')
        if isinstance(session, str) and isinstance(sequence, int) and not isinstance(sequence, bool):
            prior = self.last_sequence.get(session)
            if prior is not None and sequence < prior:
                self.out_of_order.append(event_id)
            self.last_sequence[session] = max(sequence, prior or 0)
        observed_at = observed_at or datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        for label, start, end in (
            ('eventToCallbackMs', data.get('timestamp'), data.get('callbackReceivedAt')),
            ('callbackToSseMs', data.get('callbackReceivedAt'), data.get('sseEmittedAt')),
            ('sseToObserverMs', data.get('sseEmittedAt'), observed_at),
            ('eventToObserverMs', data.get('timestamp'), observed_at),
        ):
            value = latency_ms(start, end)
            if value is not None:
                self.latencies[label].append(value)

    def report(self):
        stored_ids = set(self.stored)
        sse_ids = set(self.sse)
        baseline_ids = {ref.rsplit('/', 1)[-1][7:].lower() for ref in self.baseline_refs
                        if ref.rsplit('/', 1)[-1].startswith('webcam-')}
        def was_baseline(event_id):
            return event_id.replace('-', '').lower() in baseline_ids
        sessions = {}
        for event in self.stored.values():
            session, sequence = event['sessionId'], event['eventSequence']
            if isinstance(session, str) and isinstance(sequence, int) and not isinstance(sequence, bool):
                sessions.setdefault(session, set()).add(sequence)
        gaps = {session: sorted(set(range(min(values), max(values) + 1)) - values)
                for session, values in sessions.items() if values}
        return {'confirmedNewCins': len(stored_ids), 'repetitionSignals': self.repetition_signals,
                'uniqueSseIds': len(sse_ids), 'duplicateSseIds': sorted(k for k, n in self.sse.items() if n > 1),
                'storedWithoutSseIds': sorted(stored_ids - sse_ids),
                'sseForBaselineCinIds': sorted(k for k in sse_ids - stored_ids if was_baseline(k)),
                'sseWithoutNewStoredCinIds': sorted(k for k in sse_ids - stored_ids if not was_baseline(k)),
                'outOfOrderSseIds': self.out_of_order,
                'missingStoredSequencesBySession': gaps,
                'badSsePayloads': self.bad_sse_payloads,
                'summarySignals': self.summary_signals, 'reconnects': self.reconnects,
                'reconcileEvents': self.reconcile_events,
                'latency': {key: duration_stats(values) for key, values in self.latencies.items()}}


class CSEReader:
    def __init__(self, client, base, origin):
        self.client, self.base, self.origin = client, base.rstrip('/'), origin

    async def get(self, suffix, params=None):
        response = await self.client.get(self.base + '/' + REPETITIONS + suffix,
            params=params, headers={'X-M2M-Origin': self.origin,
                                    'X-M2M-RI': 'cindysense-reliability-read', 'X-M2M-RVI': '4'})
        response.raise_for_status()
        return response.json()

    async def refs(self, known=None):
        found = []
        for page in range(21):
            data = await self.get('', {'rcn': 6, 'ty': 4, 'lvl': 1, 'lim': 100, 'ofst': page * 100})
            entries = data.get('m2m:rrl', {}).get('rrf')
            if not isinstance(entries, list):
                raise ValueError('TinyIoT CIN listing is malformed')
            for row in entries:
                if not isinstance(row, dict) or row.get('typ') != 4:
                    raise ValueError('TinyIoT CIN reference is malformed')
                ref = row.get('val')
                if not isinstance(ref, str) or not ref.strip('/').startswith('TinyIoT/' + REPETITIONS + '/'):
                    raise ValueError('TinyIoT CIN reference is outside RepetitionCount')
                name = ref.strip('/').rsplit('/', 1)[-1]
                if name in ('', '.', '..') or '/' in name:
                    raise ValueError('TinyIoT CIN name is invalid')
                if ref in (known or set()):
                    return found
                found.append(ref)
            if len(entries) < 100:
                return found
        raise ValueError('TinyIoT CIN listing exceeded 2100 entries')

    async def content(self, ref):
        name = ref.strip('/').rsplit('/', 1)[-1]
        data = await self.get('/' + name)
        cin = data.get('m2m:cin')
        if not isinstance(cin, dict):
            raise ValueError('TinyIoT CIN readback is malformed')
        content = cin.get('con')
        if isinstance(content, str):
            content = json.loads(content)
        return content


def metric_delta(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if not before.get('instanceId') or before.get('instanceId') != after.get('instanceId'):
        return None
    result = {}
    for key in ('validatedCallbacks', 'duplicateCallbacks', 'rejectedCallbacks', 'sseQueueDrops'):
        start, finish = before.get(key), after.get(key)
        if not isinstance(start, int) or not isinstance(finish, int) or finish < start:
            return None
        result[key] = finish - start
    return result


def publisher_log_deltas(path, offset, stored_ids):
    if not path:
        return None
    try:
        with path.open(encoding='utf-8', errors='replace') as handle:
            handle.seek(offset)
            lines = handle.readlines()
    except OSError:
        return {'error': 'publisher log unavailable; detection-to-publish comparison skipped'}
    detected = {match.group(1).lower() for line in lines if (match := LOG_DETECTED.match(line))}
    confirmed = {match.group(1).lower() for line in lines if (match := LOG_CONFIRMED.match(line))}
    return {'detectedReps': len(detected), 'publisherConfirmed': len(confirmed),
            'detectedButNotConfirmedIds': sorted(detected - confirmed),
            'confirmedButNotObservedInCseIds': sorted(confirmed - stored_ids),
            'detectionQualityErrors': 'not measurable from event logs; requires physical ground truth'}


async def monitor(base, seconds, poll_interval, cse_base=None, cse_origin=None, publisher_log=None):
    ledger = EventLedger()
    result = {'durationSeconds': seconds, 'snapshotPolls': 0, 'snapshotPollFailures': 0,
              'csePolls': 0, 'csePollFailures': 0, 'metricsPollFailures': 0, 'sessions': {}}
    log_offset = publisher_log.stat().st_size if publisher_log and publisher_log.exists() else 0
    deadline = time.monotonic() + seconds
    baseline_ready = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=20.0)) as client:
        reader = CSEReader(client, cse_base, cse_origin) if cse_base and cse_origin else None

        async def metrics():
            try:
                response = await client.get(base + '/api/integration/metrics')
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                result['metricsPollFailures'] += 1
                return None

        async def scan():
            nonlocal baseline_ready
            if not reader:
                return
            try:
                refs = await reader.refs(None if not baseline_ready else ledger.seen_refs)
                if not baseline_ready:
                    ledger.baseline(refs)
                    baseline_ready = True
                else:
                    for ref in reversed(refs):
                        ledger.store(ref, await reader.content(ref))
                result['csePolls'] += 1
            except (httpx.HTTPError, ValueError, KeyError):
                result['csePollFailures'] += 1

        await scan()
        before = await metrics()

        async def poll():
            while time.monotonic() < deadline:
                try:
                    response = await client.get(base + '/api/workout/snapshot')
                    response.raise_for_status()
                    session, count = snapshot_count(response.json())
                    result['snapshotPolls'] += 1
                    if session and isinstance(count, int):
                        row = result['sessions'].setdefault(session, {'firstCount': count, 'lastCount': count})
                        row['lastCount'] = count
                except (httpx.HTTPError, ValueError, KeyError):
                    result['snapshotPollFailures'] += 1
                await scan()
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

        async def stream():
            first = True
            while time.monotonic() < deadline:
                if not first:
                    ledger.reconnects += 1
                first = False
                try:
                    async with client.stream('GET', base + '/api/integration/events') as response:
                        response.raise_for_status()
                        kind, data = None, None
                        async for line in response.aiter_lines():
                            if time.monotonic() >= deadline:
                                break
                            if not line:
                                if kind:
                                    ledger.observe(kind, data)
                                kind, data = None, None
                            elif line.startswith('event: '):
                                kind = line[7:]
                            elif line.startswith('data: '):
                                try:
                                    data = json.loads(line[6:])
                                except ValueError:
                                    data = None
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

        await asyncio.gather(poll(), stream())
        await asyncio.sleep(2)  # final reconciliation window
        await scan()
        after = await metrics()
    result.update(ledger.report())
    result['correlationStatus'] = ('unavailable' if not baseline_ready else
        'partial: reconnect or CSE read failure' if ledger.reconnects or result['csePollFailures'] else
        'no new stored webcam events' if not ledger.stored else 'observed window complete')
    result['callbackMetricsDelta'] = metric_delta(before, after)
    result['callbackFailures'] = (result['callbackMetricsDelta'] or {}).get('rejectedCallbacks')
    result['deliveryGapInterpretation'] = ('Stored CINs without SSE are callback, SSE-queue, or observer gaps; '
        'a reconnect/failed CSE poll prevents exact loss attribution.')
    result['publisher'] = publisher_log_deltas(publisher_log, log_offset, set(ledger.stored))
    result['webcamDetectionErrors'] = 'not measurable without physical ground truth'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    parser.add_argument('--cse-base', default='http://127.0.0.1:3001/TinyIoT')
    parser.add_argument('--cse-origin', default='CDashboard')
    parser.add_argument('--publisher-log', type=Path)
    parser.add_argument('--duration-seconds', type=int, default=1200)
    parser.add_argument('--poll-seconds', type=float, default=2.0)
    args = parser.parse_args()
    for value in (args.base, args.cse_base):
        parsed = urlsplit(value)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost'):
            parser.error('Only local HTTP FastAPI/TinyIoT listeners are allowed.')
    if not args.cse_origin or not 1 <= args.duration_seconds <= 7200 or not 0.2 <= args.poll_seconds <= 60:
        parser.error('Origin, duration, or poll interval is invalid.')
    print(json.dumps(asyncio.run(monitor(args.base.rstrip('/'), args.duration_seconds,
        args.poll_seconds, args.cse_base, args.cse_origin, args.publisher_log)), indent=2))


if __name__ == '__main__':
    main()
