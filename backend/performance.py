"""Observed Cindy pace from retained, validated repetition events.

Intervals are only measured between consecutive events of one exercise in one
round. A gap above 2.5 times that exercise's median interval is a possible
rest gap, not a measured rest or evidence of fatigue. Missing pose observations
cannot be distinguished from rest using repetition CINs alone.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median, pstdev

from .events import RepetitionEvent

EXERCISES = ('pullup', 'pushup', 'squat')
SET_TARGETS = {'pullup': 5, 'pushup': 10, 'squat': 15}


def _ordered_exercise_events(events, exercise):
    unique = {str(event.event_id): event for event in events if event.exercise == exercise}
    return sorted(unique.values(), key=lambda item: (item.event_sequence or 0, item.timestamp))


def session_average_paces(events, rest_multiplier=2.5, minimum_intervals=3):
    """Observed within-set pace; never count transitions or missing repetitions."""
    if len({event.session_id for event in events}) > 1:
        return {exercise: {'secondsPerRep': None, 'repsPerMinute': None,
                           'acceptedIntervalCount': 0, 'possibleIntervalCount': 0,
                           'coverage': 0, 'unavailableReason': 'mixed_sessions',
                           'rejectedIntervals': {}} for exercise in EXERCISES}
    result = {}
    for exercise in EXERCISES:
        ordered = _ordered_exercise_events(events, exercise)
        possible = 0
        candidates = []
        rejected = {'nonconsecutive': 0, 'invalid_time': 0, 'long_gap': 0}
        for before, after in zip(ordered, ordered[1:]):
            if before.round != after.round:
                continue
            possible += 1
            if (after.repetitions != before.repetitions + 1 or
                    (after.event_sequence or 0) <= (before.event_sequence or 0)):
                rejected['nonconsecutive'] += 1
                continue
            seconds = (after.timestamp - before.timestamp).total_seconds()
            if seconds <= 0:
                rejected['invalid_time'] += 1
                continue
            candidates.append(seconds)
        baseline = median(candidates) if candidates else None
        accepted = [value for value in candidates if value <= baseline * rest_multiplier] if baseline else []
        rejected['long_gap'] = len(candidates) - len(accepted)
        count = len(accepted)
        available = count >= minimum_intervals
        average = sum(accepted) / count if available else None
        result[exercise] = {
            'secondsPerRep': round(average, 3) if available else None,
            'repsPerMinute': round(60 / average, 2) if available else None,
            'acceptedIntervalCount': count,
            'possibleIntervalCount': possible,
            'coverage': round(count / possible, 3) if possible else 0,
            'unavailableReason': None if available else 'insufficient_valid_intervals',
            'rejectedIntervals': rejected,
        }
    return result


def latest_set_paces(events, rest_multiplier=2.5, rolling_intervals=4):
    """Three bounded refresh-recovery values from session-matched event history."""
    if len({event.session_id for event in events}) > 1:
        return {exercise: None for exercise in EXERCISES}
    result = {}
    for exercise in EXERCISES:
        ordered = _ordered_exercise_events(events, exercise)
        if not ordered:
            result[exercise] = None
            continue
        latest = ordered[-1]
        current = [item for item in ordered if item.round == latest.round]
        intervals = []
        measured = None
        for before, after in zip(current, current[1:]):
            seconds = (after.timestamp - before.timestamp).total_seconds()
            if (after.repetitions != before.repetitions + 1 or
                    (after.event_sequence or 0) <= (before.event_sequence or 0) or seconds <= 0):
                intervals = []
                continue
            if intervals and seconds > median(intervals) * rest_multiplier:
                intervals = []
                continue
            intervals = (intervals + [seconds])[-rolling_intervals:]
            if len(intervals) >= 2:
                measured = {'secondsPerRep': round(sum(intervals) / len(intervals), 3),
                            'repsPerMinute': round(60 * len(intervals) / sum(intervals), 2),
                            'intervalCount': len(intervals), 'measuredAt': after.timestamp.isoformat()}
        result[exercise] = {'round': latest.round, 'repetitions': latest.repetitions,
                            'lastEventAt': latest.timestamp.isoformat(),
                            'setComplete': latest.repetitions % SET_TARGETS[exercise] == 0,
                            'pace': measured}
    return result

@dataclass(frozen=True)
class PerformanceConfig:
    rest_multiplier: float = 2.5
    slowdown_ratio: float = 1.25
    minimum_pace_intervals: int = 3


@dataclass(frozen=True)
class PaceWarningConfig:
    """Conservative live-warning settings, all relative to the session pace."""

    minimum_pace_intervals: int = 7
    baseline_intervals: int = 4
    recent_intervals: int = 3
    slowdown_ratio: float = 1.35
    required_evidence: int = 2
    recovery_ratio: float = 1.15
    cooldown_seconds: int = 60
    rest_multiplier: float = 2.5
    max_events: int = 128


def analyze_performance(events: list[RepetitionEvent], config: PerformanceConfig | None = None) -> dict:
    config = config or PerformanceConfig()
    if not events:
        return {'state': 'insufficient_data', 'exercises': {}, 'rounds': [],
                'estimatedRestGaps': [], 'detectorQuality': 'unverified'}
    by_exercise = defaultdict(list)
    by_round = defaultdict(list)
    for event in events:
        by_exercise[event.exercise].append(event)
        by_round[event.round].append(event)
    exercises = {}
    average_paces = session_average_paces(events, config.rest_multiplier, config.minimum_pace_intervals)
    all_gaps = []
    states = []
    for exercise in EXERCISES:
        ordered = sorted(by_exercise[exercise], key=lambda item: item.event_sequence or 0)
        intervals = []
        for previous, current in zip(ordered, ordered[1:]):
            if previous.round != current.round or current.repetitions <= previous.repetitions:
                continue
            seconds = (current.timestamp - previous.timestamp).total_seconds()
            if seconds > 0:
                intervals.append((round(seconds / (current.repetitions - previous.repetitions), 3), current))
        baseline = median(value for value, _ in intervals) if intervals else None
        gaps = [{'exercise': exercise, 'round': event.round,
                 'afterEventSequence': event.event_sequence,
                 'observedGapSeconds': value,
                 'estimatedExcessSeconds': round(value - baseline, 3)}
                for value, event in intervals if baseline and value > baseline * config.rest_multiplier]
        all_gaps.extend(gaps)
        pace = [value for value, _ in intervals if not baseline or value <= baseline * config.rest_multiplier]
        early = mean(pace[:max(1, len(pace) // 2)]) if pace else None
        late = mean(pace[-max(1, len(pace) // 2):]) if pace else None
        state = ('insufficient_data' if len(pace) < config.minimum_pace_intervals else
                 'pace_decreasing' if late > early * config.slowdown_ratio else
                 'unexplained_long_gap' if gaps else 'stable_performance')
        states.append(state)
        exercises[exercise] = {
            'repetitionsObserved': len(ordered), 'intervalCount': len(intervals),
            'intervalsSeconds': [value for value, _ in intervals],
            'paceSecondsPerRep': round(mean(pace), 3) if pace else None,
            'repsPerMinute': round(60 / mean(pace), 2) if pace else None,
            'paceChangePercent': round((late / early - 1) * 100, 1) if early and late else None,
            'paceConsistencyCv': round(pstdev(pace) / mean(pace), 3) if len(pace) >= 2 else None,
            'state': state, 'estimatedRestGaps': gaps,
            'sessionAveragePace': average_paces[exercise],
        }
    rounds = []
    for number, round_events in sorted(by_round.items()):
        counts = {name: len([event for event in round_events if event.exercise == name]) for name in EXERCISES}
        rounds.append({'round': number, 'observedCounts': counts,
                       'firstEventAt': min(event.timestamp for event in round_events).isoformat(),
                       'lastEventAt': max(event.timestamp for event in round_events).isoformat()})
    state = ('pace_decreasing' if 'pace_decreasing' in states else
             'unexplained_long_gap' if 'unexplained_long_gap' in states else
             'stable_performance' if 'stable_performance' in states else 'insufficient_data')
    return {'state': state, 'exercises': exercises, 'rounds': rounds,
            'estimatedRestGaps': all_gaps, 'detectorQuality': 'unverified',
            'method': {'restMultiplier': config.rest_multiplier, 'slowdownRatio': config.slowdown_ratio,
                       'minimumPaceIntervals': config.minimum_pace_intervals,
                       'note': 'Gaps may reflect rest or missed webcam detections; no medical fatigue is inferred.'}}


def analyze_pace_warning(events: list[RepetitionEvent],
                         config: PaceWarningConfig | None = None) -> dict:
    """Return a relative slowdown candidate without inferring physical fatigue."""
    config = config or PaceWarningConfig()
    if not events or len({event.session_id for event in events}) != 1:
        return {'state': 'insufficient_data', 'exercise': None, 'paceChangePercent': None,
                'intervalCount': 0, 'reason': 'insufficient_session_data'}
    performance = analyze_performance(events, PerformanceConfig(
        rest_multiplier=config.rest_multiplier,
        slowdown_ratio=config.slowdown_ratio,
        minimum_pace_intervals=config.minimum_pace_intervals,
    ))
    candidates = []
    for exercise, item in performance['exercises'].items():
        intervals = item['intervalsSeconds']
        if len(intervals) < max(config.minimum_pace_intervals,
                                config.baseline_intervals + config.recent_intervals):
            continue
        baseline = mean(intervals[:config.baseline_intervals])
        recent = mean(intervals[-config.recent_intervals:])
        change = round((recent / baseline - 1) * 100, 1) if baseline else None
        if change is not None and change >= (config.slowdown_ratio - 1) * 100:
            candidates.append((change, exercise, item))
    if not candidates:
        return {'state': 'pace_stable' if any(
            item['intervalCount'] >= config.minimum_pace_intervals
            for item in performance['exercises'].values()) else 'insufficient_data',
            'exercise': None, 'paceChangePercent': None, 'intervalCount': max(
                (item['intervalCount'] for item in performance['exercises'].values()), default=0),
            'reason': 'no_sustained_relative_slowdown'}
    change, exercise, item = max(candidates)
    return {'state': 'pace_slowing', 'exercise': exercise,
            'paceChangePercent': change, 'intervalCount': item['intervalCount'],
            'reason': 'sustained_relative_slowdown'}


class PaceWarningAnalyzer:
    """Bounded, nonblocking live warning state for one active workout stream."""

    def __init__(self, config: PaceWarningConfig | None = None):
        self.config = config or PaceWarningConfig()
        self.session_id: str | None = None
        self.events: list[RepetitionEvent] = []
        self.seen_ids: set[str] = set()
        self.evidence: dict[str, int] = defaultdict(int)
        self.armed: dict[str, bool] = defaultdict(lambda: True)
        self.last_warning_at: dict[str, datetime] = {}

    def reset(self) -> None:
        self.session_id = None
        self.events.clear()
        self.seen_ids.clear()
        self.evidence.clear()
        self.armed.clear()
        self.last_warning_at.clear()

    def process(self, event: RepetitionEvent) -> dict | None:
        if event.source != 'webcam':
            return None
        if self.session_id != event.session_id:
            self.reset()
            self.session_id = event.session_id
        event_id = str(event.event_id)
        if event_id in self.seen_ids:
            return None
        self.seen_ids.add(event_id)
        self.events.append(event)
        self.events = sorted(self.events, key=lambda item: (item.event_sequence or 0, item.timestamp, str(item.event_id)))[-self.config.max_events:]
        analysis = analyze_pace_warning(self.events, self.config)
        exercise = event.exercise
        if analysis['state'] != 'pace_slowing' or analysis['exercise'] != exercise:
            if analysis['state'] == 'pace_stable':
                self.evidence[exercise] = 0
                self.armed[exercise] = True
            return None
        self.evidence[exercise] += 1
        if self.evidence[exercise] < self.config.required_evidence or not self.armed[exercise]:
            return None
        previous = self.last_warning_at.get(exercise)
        if previous is not None and (event.timestamp - previous).total_seconds() < self.config.cooldown_seconds:
            return None
        self.armed[exercise] = False
        self.last_warning_at[exercise] = event.timestamp
        return {
            'warningId': f'pace-warning-{event.session_id}-{exercise}-{event_id}',
            'sessionId': event.session_id,
            'exercise': exercise,
            'message': 'Your pace is slowing down. Consider taking a short rest.',
            'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'observedAt': event.timestamp.isoformat(),
            'paceChangePercent': analysis['paceChangePercent'],
            'evidence': self.evidence[exercise],
            'cooldownSeconds': self.config.cooldown_seconds,
            'medicalDisclaimer': 'Non-medical performance warning; pace may vary naturally.',
        }
