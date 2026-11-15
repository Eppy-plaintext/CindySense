import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.events import RepetitionEvent
from backend.performance import analyze_performance
from backend.workout_history import build_history


def events(gaps, exercise='pushup'):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    result = []
    for number, gap in enumerate(gaps, 1):
        now += timedelta(seconds=gap)
        result.append(RepetitionEvent(eventId=str(uuid4()), eventSequence=number,
                                      sessionId='webcam-real', exercise=exercise,
                                      repetitions=number, round=1,
                                      timestamp=now.isoformat(), source='webcam'))
    return result


class PerformanceTests(unittest.TestCase):
    def test_stable_and_insufficient_data(self):
        stable = analyze_performance(events([2] * 8))
        self.assertEqual(stable['state'], 'stable_performance')
        self.assertEqual(stable['exercises']['pushup']['repsPerMinute'], 30)
        self.assertEqual(analyze_performance(events([2, 2]))['state'], 'insufficient_data')

    def test_slowdown_and_uncertain_rest_gap(self):
        slow = analyze_performance(events([2, 2, 2, 2, 3, 3, 3, 3]))
        self.assertEqual(slow['state'], 'pace_decreasing')
        gap = analyze_performance(events([2, 2, 2, 2, 12, 2, 2, 2]))
        self.assertEqual(gap['state'], 'unexplained_long_gap')
        self.assertEqual(gap['detectorQuality'], 'unverified')
        self.assertEqual(len(gap['estimatedRestGaps']), 1)

    def test_incomplete_round_does_not_create_cross_round_interval(self):
        first = events([2, 2, 2, 2])
        next_round = events([2, 2, 2, 2])
        shifted = [event.model_copy(update={'round': 2, 'event_sequence': index + 4,
                                            'timestamp': event.timestamp + timedelta(seconds=30)})
                   for index, event in enumerate(next_round, 1)]
        result = analyze_performance(first + shifted)
        self.assertEqual(result['exercises']['pushup']['intervalCount'], 6)
        self.assertEqual(len(result['rounds']), 2)


class HistoryTests(unittest.TestCase):
    def test_only_matching_full_duration_workouts_are_comparable(self):
        observed = {'retainedInstanceCount': 3, 'exclusionReasons': {},
                    'availableSessions': [
                        {'sessionId': key, 'exerciseCounts': {'pullup': 5, 'pushup': 10, 'squat': 15},
                         'eventCount': 30, 'suitable': True,
                         'lastEventAt': f'2026-11-1{index}T00:00:00+00:00',
                         'performance': {'state': 'stable_performance'}}
                        for index, key in enumerate(('full', 'stopped', 'orphan'), 1)]}
        summaries = [{'workoutId': 'full', 'source': 'webcam', 'completionReason': 'timer',
                      'durationSeconds': 1200, 'roundsCompleted': 1,
                      'reps': {'pullup': 5, 'pushup': 10, 'squat': 15}},
                     {'workoutId': 'stopped', 'source': 'webcam', 'completionReason': 'explicit_stop',
                      'durationSeconds': 180, 'roundsCompleted': 1,
                      'reps': {'pullup': 5, 'pushup': 10, 'squat': 15}}]
        result = build_history(observed, summaries, {'targetRounds': 5})
        self.assertEqual(result['comparableSessionCount'], 1)
        by_id = {row['sessionId']: row for row in result['sessions']}
        self.assertEqual(by_id['full']['targetProgressPercent'], 20)
        self.assertEqual(by_id['stopped']['status'], 'stopped_early')
        self.assertEqual(by_id['orphan']['status'], 'no_verified_summary')
        self.assertIsNone(by_id['orphan']['completedRounds'])

    def test_mismatch_and_missing_retained_events_are_not_compared(self):
        observed = {'retainedInstanceCount': 1, 'exclusionReasons': {},
                    'availableSessions': [{'sessionId': 'real', 'exerciseCounts': {'pullup': 5},
                                           'lastEventAt': '2026-11-16T00:00:00+00:00'}]}
        summaries = [{'workoutId': 'real', 'source': 'webcam', 'completionReason': 'timer',
                      'durationSeconds': 1200, 'roundsCompleted': 1, 'reps': {'pullup': 6}},
                     {'workoutId': 'retained-out', 'source': 'webcam', 'completionReason': 'timer',
                      'durationSeconds': 1200, 'roundsCompleted': 2}]
        result = build_history(observed, summaries)
        self.assertEqual(result['comparableSessionCount'], 0)
        self.assertEqual({row['status'] for row in result['sessions']},
                         {'summary_event_mismatch', 'summary_without_retained_events'})

    def test_full_duration_with_only_one_repetition_is_not_a_comparison(self):
        observed = {'retainedInstanceCount': 1, 'exclusionReasons': {}, 'availableSessions': [
            {'sessionId': 'partial', 'exerciseCounts': {'pullup': 1, 'pushup': 0, 'squat': 0},
             'eventCount': 1, 'suitable': False, 'lastEventAt': '2026-11-16T00:00:00+00:00'}]}
        summaries = [{'workoutId': 'partial', 'source': 'webcam', 'completionReason': 'timer',
                      'durationSeconds': 1200, 'roundsCompleted': 0,
                      'reps': {'pullup': 1, 'pushup': 0, 'squat': 0}}]
        result = build_history(observed, summaries)
        self.assertEqual(result['comparableSessionCount'], 0)
        self.assertEqual(result['sessions'][0]['status'], 'completed_20_minute')
