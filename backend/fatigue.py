"""Conservative, non-medical fatigue signals from validated repetition events."""

from dataclasses import dataclass
from datetime import datetime
from statistics import mean, median
from typing import Mapping, Sequence

from .events import RepetitionEvent

EXERCISES = ('pullup', 'pushup', 'squat')


@dataclass(frozen=True)
class FatigueConfig:
    minimum_repetitions: int = 5
    baseline_repetitions: int = 5
    rolling_repetitions: int = 5
    pause_multiplier: float = 2.5
    possible_score: float = 20.0
    elevated_score: float = 50.0
    tracking_quality_threshold: float = 0.7


def _unknown(session_id: str | None, exercise: str, reason: str,
             excluded: list[dict] | None = None) -> dict:
    return {
        'sessionId': session_id,
        'exercise': exercise,
        'baselinePaceSecondsPerRep': None,
        'currentPaceSecondsPerRep': None,
        'baselinePauseSeconds': None,
        'currentPauseSeconds': None,
        'pauseChangeSeconds': None,
        'fatigueScore': None,
        'fatigueLevel': 'unknown',
        'contributingSignals': [],
        'excludedIntervals': excluded or [],
        'reasonUnknown': reason,
        'medicalDisclaimer': 'Heuristic performance signal; not medically validated fatigue detection.',
    }


def _valid_timestamp(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _quality_ok(event: RepetitionEvent, quality_by_event_id: Mapping[str, object] | None,
                config: FatigueConfig) -> bool:
    if quality_by_event_id is None or str(event.event_id) not in quality_by_event_id:
        return True
    value = quality_by_event_id[str(event.event_id)]
    if isinstance(value, bool):
        return value
    return isinstance(value, (int, float)) and value >= config.tracking_quality_threshold


def _intervals(events: Sequence[RepetitionEvent], config: FatigueConfig) -> tuple[list[float], list[float], list[dict]]:
    usable: list[float] = []
    pauses: list[float] = []
    excluded: list[dict] = []
    if len(events) < 2:
        return usable, pauses, excluded
    provisional = median(
        (events[index].timestamp - events[index - 1].timestamp).total_seconds() /
        max(1, events[index].repetitions - events[index - 1].repetitions)
        for index in range(1, len(events))
        if events[index].round == events[index - 1].round and
        events[index].repetitions > events[index - 1].repetitions and
        (events[index].timestamp - events[index - 1].timestamp).total_seconds() > 0
    ) if any(
        events[index].round == events[index - 1].round and
        events[index].repetitions > events[index - 1].repetitions and
        (events[index].timestamp - events[index - 1].timestamp).total_seconds() > 0
        for index in range(1, len(events))
    ) else None
    for index in range(1, len(events)):
        previous, current = events[index - 1], events[index]
        if current.round != previous.round:
            excluded.append({'eventId': str(current.event_id), 'reason': 'exercise_transition_or_round_boundary'})
            continue
        delta = current.repetitions - previous.repetitions
        seconds = (current.timestamp - previous.timestamp).total_seconds()
        if delta <= 0 or seconds <= 0:
            excluded.append({'eventId': str(current.event_id), 'reason': 'non_monotonic_repetition_or_timestamp'})
            continue
        interval = seconds / delta
        if provisional and interval > provisional * config.pause_multiplier:
            pauses.append(max(0.0, interval - provisional))
            excluded.append({'eventId': str(current.event_id), 'reason': 'possible_pause', 'seconds': round(interval, 3)})
        else:
            usable.append(interval)
    return usable, pauses, excluded


def _estimate_exercise(session_id: str, exercise: str, events: list[RepetitionEvent],
                       config: FatigueConfig,
                       quality_by_event_id: Mapping[str, object] | None) -> dict:
    excluded: list[dict] = []
    usable_events = []
    for event in events:
        if not _quality_ok(event, quality_by_event_id, config):
            excluded.append({'eventId': str(event.event_id), 'reason': 'insufficient_tracking_quality'})
        else:
            usable_events.append(event)
    if len(usable_events) != len(events):
        return _unknown(session_id, exercise, 'tracking_quality_insufficient', excluded)
    if len(usable_events) < config.minimum_repetitions:
        return _unknown(session_id, exercise, 'insufficient_valid_repetitions', excluded)
    if any(current.repetitions <= previous.repetitions
           for previous, current in zip(usable_events, usable_events[1:])):
        return _unknown(session_id, exercise, 'non_monotonic_repetition_or_timestamp', excluded)
    baseline_events = usable_events[:config.baseline_repetitions]
    # Include the preceding event so a pause immediately before the rolling
    # window is measured rather than silently dropped at the slice boundary.
    current_events = usable_events[-(config.rolling_repetitions + 1):]
    baseline_intervals, baseline_pauses, baseline_excluded = _intervals(baseline_events, config)
    current_intervals, current_pauses, current_excluded = _intervals(current_events, config)
    excluded.extend(baseline_excluded + current_excluded)
    if not baseline_intervals or not current_intervals:
        return _unknown(session_id, exercise, 'insufficient_valid_intervals', excluded)
    baseline_pace = mean(baseline_intervals)
    current_pace = mean(current_intervals)
    slowdown = max(0.0, (current_pace - baseline_pace) / baseline_pace)
    baseline_pause = median(baseline_pauses) if baseline_pauses else 0.0
    current_pause = median(current_pauses) if current_pauses else 0.0
    pause_change = max(0.0, current_pause - baseline_pause)
    pace_signal = min(1.0, slowdown / 0.5)
    pause_signal = min(1.0, pause_change / max(baseline_pace, 1.0))
    score = round(100 * (0.7 * pace_signal + 0.3 * pause_signal), 1)
    level = ('normal' if score < config.possible_score else
             'possible_fatigue' if score < config.elevated_score else 'elevated_fatigue')
    signals = []
    if slowdown >= 0.15:
        signals.append('pace_slowdown')
    if current_pause > baseline_pause and current_pause > 0:
        signals.append('pause_duration_increase')
    return {
        'sessionId': session_id,
        'exercise': exercise,
        'baselinePaceSecondsPerRep': round(baseline_pace, 3),
        'currentPaceSecondsPerRep': round(current_pace, 3),
        'baselinePauseSeconds': round(baseline_pause, 3),
        'currentPauseSeconds': round(current_pause, 3),
        'pauseChangeSeconds': round(pause_change, 3),
        'fatigueScore': score,
        'fatigueLevel': level,
        'contributingSignals': signals,
        'excludedIntervals': excluded,
        'reasonUnknown': None,
        'medicalDisclaimer': 'Heuristic performance signal; not medically validated fatigue detection.',
    }


def estimate_fatigue(events: Sequence[RepetitionEvent], config: FatigueConfig | None = None,
                     quality_by_event_id: Mapping[str, object] | None = None,
                     require_quality: bool = False) -> list[dict]:
    """Estimate fatigue per exercise without inferring from missing observations."""
    config = config or FatigueConfig()
    event_list = list(events)
    sessions = {event.session_id for event in event_list}
    session_id = next(iter(sessions), None) if len(sessions) == 1 else None
    if not event_list:
        return [_unknown(None, exercise, 'no_validated_events') for exercise in EXERCISES]
    if len(sessions) != 1:
        return [_unknown(session_id, exercise, 'session_identity_changed') for exercise in EXERCISES]
    if require_quality and (quality_by_event_id is None or any(
            str(event.event_id) not in quality_by_event_id for event in event_list)):
        return [_unknown(session_id, exercise, 'detector_quality_unverified') for exercise in EXERCISES]
    if any(event.source != 'webcam' for event in event_list):
        return [_unknown(session_id, exercise, 'unsupported_or_mixed_event_source') for exercise in EXERCISES]
    if any(not _valid_timestamp(event.timestamp) for event in event_list):
        return [_unknown(session_id, exercise, 'missing_or_invalid_timestamp') for exercise in EXERCISES]
    sequences = [event.event_sequence for event in event_list]
    if any(sequence is None for sequence in sequences) or any(
            sequences[index] <= sequences[index - 1] for index in range(1, len(sequences))):
        return [_unknown(session_id, exercise, 'event_sequence_reset_or_out_of_order') for exercise in EXERCISES]
    if any(event.timestamp < event_list[index - 1].timestamp for index, event in enumerate(event_list) if index):
        return [_unknown(session_id, exercise, 'event_timestamp_out_of_order') for exercise in EXERCISES]
    return [_estimate_exercise(session_id, exercise,
                               [event for event in event_list if event.exercise == exercise],
                               config, quality_by_event_id)
            for exercise in EXERCISES]
