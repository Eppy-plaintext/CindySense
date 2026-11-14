"""Read-only, session-scoped repetition history for fatigue analysis."""

import asyncio
from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import datetime
from typing import Any

from .events import RepetitionEvent, classify_repetition_content
from .fatigue import EXERCISES, FatigueConfig, estimate_fatigue
from .tinyiot import TinyIoTClient, TinyIoTError

DIAGNOSTIC_SESSION_PREFIXES = ('auto-', 'diag', 'live-', 'sse-audit', 'test-')


def _add_reason(reasons: Counter, reason: str) -> None:
    reasons[reason] += 1


def _is_diagnostic(content: dict[str, Any], event: RepetitionEvent | None = None) -> str | None:
    if content.get('synthetic') is True:
        return 'synthetic'
    source = content.get('source')
    if source is not None and source != 'webcam':
        return 'non_webcam_or_diagnostic_source'
    session_id = event.session_id if event is not None else content.get('sessionId')
    if source == 'webcam' and isinstance(session_id, str) and session_id.startswith(DIAGNOSTIC_SESSION_PREFIXES):
        return 'diagnostic_session'
    return None


def _session_summary(session_id: str, events: list[RepetitionEvent], config: FatigueConfig) -> dict:
    counts = Counter(event.exercise for event in events)
    first = min(event.timestamp for event in events)
    last = max(event.timestamp for event in events)
    suitable = all(counts[exercise] >= config.minimum_repetitions for exercise in EXERCISES)
    return {
        'sessionId': session_id,
        'eventCount': len(events),
        'exerciseCounts': {exercise: counts[exercise] for exercise in EXERCISES},
        'firstEventAt': first.isoformat(),
        'lastEventAt': last.isoformat(),
        'suitable': suitable,
    }


async def read_fatigue_history(client: TinyIoTClient, resource: str, origin: str,
                               selected_session_id: str | None = None,
                               config: FatigueConfig | None = None,
                               container: dict | None = None) -> dict:
    """Read and validate all retained CINs without changing existing snapshot behavior."""
    config = config or FatigueConfig()
    container = container or await client.retrieve(resource, origin)
    metadata = container.get('m2m:cnt')
    if not isinstance(metadata, dict):
        raise TinyIoTError('invalid_resource', 'Repetition resource is not a container.', 502)
    retained_count = metadata.get('cni') if isinstance(metadata.get('cni'), int) else None
    refs = await client.list_content_instances(resource, origin, expected_count=retained_count)
    reasons: Counter = Counter()
    grouped: dict[str, dict[str, RepetitionEvent]] = {}

    for ref in refs:
        try:
            payload = await client.retrieve(ref, origin)
            content, _ = client.parse_content_instance(payload)
        except TinyIoTError:
            _add_reason(reasons, 'content_retrieval_error')
            continue
        if not isinstance(content, dict):
            _add_reason(reasons, 'legacy_or_non_structured_content')
            continue
        diagnostic_reason = _is_diagnostic(content)
        if diagnostic_reason:
            _add_reason(reasons, diagnostic_reason)
            continue
        try:
            classification, event, _ = classify_repetition_content(content)
        except ValueError:
            _add_reason(reasons, 'malformed_repetition_event')
            continue
        if classification != 'structured_event' or event is None:
            _add_reason(reasons, 'legacy_or_non_structured_content')
            continue
        diagnostic_reason = _is_diagnostic(content, event)
        if diagnostic_reason:
            _add_reason(reasons, diagnostic_reason)
            continue
        by_id = grouped.setdefault(event.session_id, {})
        event_id = str(event.event_id)
        if event_id in by_id:
            if by_id[event_id].same_as(event):
                _add_reason(reasons, 'duplicate_event')
            else:
                by_id.pop(event_id, None)
                _add_reason(reasons, 'duplicate_event_conflict')
            continue
        by_id[event_id] = event

    sessions = []
    for session_id, event_map in grouped.items():
        if event_map:
            sessions.append(_session_summary(session_id, list(event_map.values()), config))
    sessions.sort(key=lambda item: item['lastEventAt'], reverse=True)
    session_map = {item['sessionId']: item for item in sessions}
    selection_error = None
    if selected_session_id is not None:
        if selected_session_id not in session_map:
            selection_error = 'requested_session_not_found'
            selected = None
        else:
            selected = selected_session_id
    else:
        suitable = [item for item in sessions if item['suitable']]
        selected = suitable[0]['sessionId'] if suitable else (sessions[0]['sessionId'] if sessions else None)
        if selected is not None and not session_map[selected]['suitable']:
            selection_error = 'no_session_has_minimum_repetitions_for_all_exercises'

    selected_events = [] if selected is None else sorted(
        grouped[selected].values(), key=lambda event: (event.event_sequence or 0, event.timestamp, str(event.event_id)))
    results = estimate_fatigue(selected_events, config=config)
    # A workout-level fatigue result is sufficient only when every supported
    # exercise has enough validated history.  One exercise alone must not make
    # the endpoint appear ready for a complete workout assessment.
    data_sufficient = bool(selected_events) and all(item['fatigueLevel'] != 'unknown' for item in results)
    return {
        'retainedInstanceCount': retained_count,
        'retrievedInstanceCount': len(refs),
        'validEventCount': sum(len(event_map) for event_map in grouped.values()),
        'excludedEventCount': sum(reasons.values()),
        'selectedSessionId': selected,
        'availableSessions': sessions,
        'dataSufficient': data_sufficient,
        'exclusionReasons': dict(reasons),
        'selectionError': selection_error,
        'selectedEventCount': len(selected_events),
        'results': results,
    }


class FatigueHistoryCache:
    """Bounded, session-scoped, single-flight cache for expensive history scans.

    The latest-container marker is read before consulting the cache.  A changed
    latest CIN identity or retained count therefore invalidates the entry while
    keeping dashboard polling from rescanning all retained CINs every time.
    """

    def __init__(self, max_entries: int = 8):
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = asyncio.Lock()
        self.cache_hits = 0
        self.full_scans = 0

    @staticmethod
    def _marker(container: dict, latest: dict) -> tuple:
        metadata = container.get('m2m:cnt')
        if not isinstance(metadata, dict):
            raise TinyIoTError('invalid_resource', 'Repetition resource is not a container.', 502)
        retained_count = metadata.get('cni') if isinstance(metadata.get('cni'), int) else None
        _, instance = TinyIoTClient.parse_content_instance(latest)
        latest_identity = instance.get('ri') or instance.get('rn') or instance.get('ct')
        if latest_identity is None:
            raise TinyIoTError('invalid_tinyiot_response', 'Latest repetition identity is unavailable.', 502)
        return retained_count, str(latest_identity), instance.get('cni')

    async def read(self, client: TinyIoTClient, resource: str, origin: str,
                   selected_session_id: str | None = None,
                   config: FatigueConfig | None = None) -> dict:
        async with self._lock:
            container = await client.retrieve(resource, origin)
            latest = await client.retrieve(f'{resource}/la', origin)
            marker = self._marker(container, latest)
            key = (resource, selected_session_id, marker)
            cached = self._entries.get(key)
            if cached is not None:
                self.cache_hits += 1
                self._entries.move_to_end(key)
                return deepcopy(cached)

            self.full_scans += 1
            result = await read_fatigue_history(
                client, resource, origin, selected_session_id=selected_session_id,
                config=config, container=container)
            self._entries[key] = deepcopy(result)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return deepcopy(result)

    def stats(self) -> dict:
        return {'cacheHits': self.cache_hits, 'fullScans': self.full_scans,
                'entries': len(self._entries)}

    def clear(self) -> None:
        self._entries.clear()
