"""Observed Cindy pace from retained, validated repetition events.

Intervals are only measured between consecutive events of one exercise in one
round. A gap above 2.5 times that exercise's median interval is a possible
rest gap, not a measured rest or evidence of fatigue. Missing pose observations
cannot be distinguished from rest using repetition CINs alone.
"""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, median, pstdev

from .events import RepetitionEvent

EXERCISES = ('pullup', 'pushup', 'squat')

@dataclass(frozen=True)
class PerformanceConfig:
    rest_multiplier: float = 2.5
    slowdown_ratio: float = 1.25
    minimum_pace_intervals: int = 3


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
