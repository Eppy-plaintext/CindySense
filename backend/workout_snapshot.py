"""Bounded aggregation of retained oneM2M repetition events for the live dashboard."""

import asyncio
from datetime import datetime, timezone
import re
import time

from .events import RepetitionEvent, classify_repetition_content, deduplicate_and_order
from .performance import latest_set_paces
from .tinyiot import TinyIoTClient, TinyIoTError

TARGETS = {'pullup': 5, 'pushup': 10, 'squat': 15}
MAX_CACHED_EVENTS = 2000
PACE_TAIL_PER_EXERCISE = 8
BASELINE_EVENTS_PER_EXERCISE = 5
WEBCAM_SESSION_TIME = re.compile(r'^webcam-(\d{8}T\d{6}Z)-')


def _session_start(event: RepetitionEvent) -> datetime:
    if event.source == 'webcam':
        match = WEBCAM_SESSION_TIME.match(event.session_id)
        if match:
            try:
                return datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return event.timestamp


def summarize_events(events: list[RepetitionEvent]) -> dict:
    ordered = deduplicate_and_order(events)
    by_source: dict[str, dict[str, list[RepetitionEvent]]] = {'webcam': {}, 'mpu6050': {}}
    for event in ordered:
        if event.source in by_source:
            by_source[event.source].setdefault(event.session_id, []).append(event)

    sources: dict[str, dict | None] = {}
    for source, sessions in by_source.items():
        if not sessions:
            sources[source] = None
            continue
        session_id, session_events = max(
            sessions.items(), key=lambda item: (
                min(_session_start(event) for event in item[1]),
                max(event.timestamp for event in item[1]), item[0],
            )
        )
        counts = {name: 0 for name in TARGETS}
        observed = set()
        for event in session_events:
            counts[event.exercise] = max(counts[event.exercise], event.repetitions)
            observed.add(event.exercise)
        latest_at = max(event.timestamp for event in session_events)
        start_at = min(_session_start(event) for event in session_events)
        # The dashboard needs occurrence times after a refresh. Reuse the
        # validated events already fetched for counts; do not add CIN reads.
        recent_events = []
        baseline_events = []
        for exercise in TARGETS:
            exercise_events = sorted(
                (event for event in session_events if event.exercise == exercise),
                key=lambda event: (event.event_sequence or 0, event.timestamp, str(event.event_id)),
            )
            def occurrence(event: RepetitionEvent) -> dict:
                return {
                    'eventId': str(event.event_id),
                    'eventSequence': event.event_sequence,
                    'exercise': event.exercise,
                    'repetitions': event.repetitions,
                    'round': event.round,
                    'timestamp': event.timestamp.isoformat(),
                }
            recent_events.extend(occurrence(event) for event in exercise_events[-PACE_TAIL_PER_EXERCISE:])
            baseline_events.extend(occurrence(event) for event in exercise_events[:BASELINE_EVENTS_PER_EXERCISE])
        sources[source] = {
            'sessionId': session_id,
            'counts': counts,
            'completedRounds': min(counts[name] // target for name, target in TARGETS.items()),
            'startAt': start_at.isoformat(),
            'lastEventAt': latest_at.isoformat(),
            'observedExercises': sorted(observed),
            'startInferred': source != 'webcam' or WEBCAM_SESSION_TIME.match(session_id) is None,
            'eventCount': len(session_events),
            'recentEvents': recent_events,
            'baselineEvents': baseline_events,
            'setPaces': latest_set_paces(session_events),
        }
    return {'sources': sources, 'cachedEventCount': len(ordered)}


class WorkoutSnapshot:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._events: dict[str, RepetitionEvent | None] = {}
        self._container = {}
        self._container_checked = 0.0
        self._revision = 0
        self._retained_refs: list[str] = []

    def validated_events(self) -> list[RepetitionEvent]:
        """Return the currently cached validated structured events without writing."""
        return [event for event in self._events.values() if event is not None]

    async def read(self, tinyiot: TinyIoTClient, resource: str, origin: str) -> dict:
        async with self._lock:
            started = time.perf_counter()
            if started - self._container_checked > 10.0:
                container = await tinyiot.retrieve(resource, origin)
                metadata = container.get('m2m:cnt')
                if not isinstance(metadata, dict):
                    raise TinyIoTError('invalid_resource', 'Repetition resource is not a container.', 502)
                self._container = {key: metadata.get(key) for key in ('mni', 'mbs', 'cni', 'cbs')}
                self._container_checked = time.perf_counter()
            refs = await tinyiot.list_content_instances(
                resource, origin, stop_at=set(self._retained_refs) if self._retained_refs else None,
                expected_count=self._container.get('cni'))
            if not refs:
                self._retained_refs = []
            else:
                self._retained_refs = list(dict.fromkeys(refs + self._retained_refs))
                mni = self._container.get('mni')
                if isinstance(mni, int) and mni > 0:
                    self._retained_refs = self._retained_refs[:mni]
            missing = [ref for ref in refs if ref not in self._events]
            if len(self._events) + len(missing) > MAX_CACHED_EVENTS:
                raise TinyIoTError('event_cache_limit', 'Repetition event cache limit reached; start a fresh backend process.', 503)
            semaphore = asyncio.Semaphore(8)

            async def fetch(ref: str):
                async with semaphore:
                    payload = await tinyiot.retrieve(ref, origin)
                    content, _ = tinyiot.parse_content_instance(payload)
                    if (isinstance(content, dict) and content.get('synthetic') is True
                            and content.get('source') == 'live-audit'):
                        return ref, None
                    try:
                        kind, event, _ = classify_repetition_content(content)
                    except ValueError as exc:
                        raise TinyIoTError('invalid_content', f'Malformed repetition content at {ref}.', 502) from exc
                    return ref, event if kind == 'structured_event' else None

            fetched = await asyncio.gather(*(fetch(ref) for ref in missing))
            self._events.update(fetched)
            try:
                result = summarize_events([event for event in self._events.values() if event is not None])
            except ValueError as exc:
                raise TinyIoTError('duplicate_event_conflict', str(exc), 502) from exc
            self._revision += 1
            result['available'] = True
            result['source'] = 'TinyIoT'
            result['retainedInstanceCount'] = len(self._retained_refs)
            result['cacheOnlyEvents'] = sum(ref not in self._retained_refs and event is not None for ref, event in self._events.items())
            result['retention'] = self._container.copy()
            mni = self._container.get('mni')
            result['retentionAtCapacity'] = isinstance(mni, int) and len(self._retained_refs) >= mni
            result['retrievedAt'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            result['retrievalMs'] = round((time.perf_counter() - started) * 1000)
            result['snapshotRevision'] = self._revision
            return result
