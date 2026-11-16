import unittest
from datetime import datetime, timezone
from math import inf, nan
from uuid import uuid4

from backend.events import RepetitionEvent
from backend.sdt import PaceMonitoring, build_device_sdt


def event(number, exercise='squat'):
    return RepetitionEvent(eventId=uuid4(), eventSequence=number, sessionId='webcam-test',
                           exercise=exercise, repetitions=number, round=1,
                           timestamp=(datetime(2026, 11, 16, 1, 0, number, tzinfo=timezone.utc)).isoformat(),
                           source='webcam')


class SdtTests(unittest.TestCase):
    def snapshot(self):
        return {'sources': {'webcam': {'sessionId': 'webcam-test', 'counts': {'squat': 2},
                                       'completedRounds': 0, 'startAt': '2026-11-16T00:59:00+00:00'}}}

    def test_annex_c_shape_and_mapping(self):
        result = build_device_sdt(
            {'sources': {'webcam': {'sessionId': 'webcam-test', 'counts': {'squat': 2},
                                    'completedRounds': 0, 'startAt': '2026-11-16T00:59:00+00:00'}}},
            [event(1), event(2)], goal={'targetRounds': 3},
            recommendation={'recommendation': 'Keep form consistent.'},
        )
        self.assertEqual(result['representation'], 'ts-0023-annex-c-json')
        self.assertEqual(result['cindySenseWorkoutTracker']['exerciseTracking']['repetitionCount'], 2)
        self.assertEqual(result['cindySenseWorkoutTracker']['coaching']['roundsRemaining'], 3)

    def test_missing_sources_are_unknown_not_zero(self):
        result = build_device_sdt(None, [])['cindySenseWorkoutTracker']
        self.assertIsNone(result['exerciseTracking']['repetitionCount'])
        self.assertIsNone(result['workoutSession']['sessionId'])
        self.assertEqual(result['paceMonitoring']['warningStatus'], 'unknown')

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            build_device_sdt({'sources': {'webcam': {'counts': {'squat': -1}}}}, [])

    def test_signed_slowdown_percent_is_valid(self):
        for value in (-11.6, 0.0, 24.5):
            self.assertEqual(PaceMonitoring.model_validate({'slowdownPercent': value}).slowdownPercent, value)

    def test_non_finite_slowdown_percent_is_invalid(self):
        for value in (nan, inf, -inf):
            with self.assertRaises(ValueError):
                PaceMonitoring.model_validate({'slowdownPercent': value})

    def test_invalid_coaching_module_does_not_discard_other_modules(self):
        result = build_device_sdt(
            {'sources': {'webcam': {'sessionId': 'webcam-test', 'counts': {'squat': 2}, 'completedRounds': 0}}},
            [event(1), event(2)], recommendation={'recommendation': 123},
        )
        device = result['cindySenseWorkoutTracker']
        self.assertEqual(device['exerciseTracking']['repetitionCount'], 2)
        self.assertEqual(device['workoutSession']['sessionId'], 'webcam-test')
        self.assertIsNone(device['coaching']['recommendation'])
        self.assertTrue(any(item['module'] == 'coaching' for item in result['mappingErrors']))

    def test_warning_is_session_scoped(self):
        result = build_device_sdt(
            {'sources': {'webcam': {'sessionId': 'webcam-test', 'counts': {'squat': 1}, 'completedRounds': 0}}},
            [event(1)], warning={'sessionId': 'different', 'timestamp': '2026-11-16T01:00:01+00:00'},
        )['cindySenseWorkoutTracker']
        self.assertEqual(result['paceMonitoring']['warningStatus'], 'none')

    def test_status_is_running_only_with_active_worker_evidence(self):
        result = build_device_sdt(self.snapshot(), [event(1)], worker={'state': 'running', 'sessionId': 'webcam-test'})
        self.assertEqual(result['cindySenseWorkoutTracker']['workoutSession']['sessionStatus'], 'running')

    def test_completed_summary_overrides_historical_event_only_session(self):
        result = build_device_sdt(self.snapshot(), [event(1)], summaries=[{
            'workoutId': 'webcam-test', 'durationSeconds': 742, 'roundsCompleted': 1,
        }])
        device = result['cindySenseWorkoutTracker']
        self.assertEqual(device['workoutSession']['sessionStatus'], 'completed')
        self.assertEqual(device['workoutSession']['durationSeconds'], 742.0)

    def test_timer_summary_remains_completed_after_worker_exit(self):
        result = build_device_sdt(self.snapshot(), [event(1)],
                                  worker={'state': 'stopped', 'sessionId': 'webcam-test'}, summaries=[{
                                      'workoutId': 'webcam-test', 'durationSeconds': 1200,
                                      'roundsCompleted': 1, 'completionReason': 'timer',
                                  }])
        self.assertEqual(result['cindySenseWorkoutTracker']['workoutSession']['sessionStatus'], 'completed')

    def test_stopped_worker_is_not_reported_as_running(self):
        result = build_device_sdt(self.snapshot(), [event(1)], worker={'state': 'stopped', 'sessionId': 'webcam-test'})
        self.assertEqual(result['cindySenseWorkoutTracker']['workoutSession']['sessionStatus'], 'stopped')

    def test_stale_event_session_is_unknown_without_worker_or_summary(self):
        result = build_device_sdt(self.snapshot(), [event(1)])
        self.assertEqual(result['cindySenseWorkoutTracker']['workoutSession']['sessionStatus'], 'unknown')

    def test_pace_state_matches_latest_exercise_not_session_aggregate(self):
        result = build_device_sdt(self.snapshot(), [event(1)], worker={'state': 'running', 'sessionId': 'webcam-test'})
        pace = result['cindySenseWorkoutTracker']['paceMonitoring']
        self.assertEqual(pace['performanceState'], 'insufficient_data')

    def test_exercise_state_is_not_mapped_to_global_slowdown(self):
        events = [event(1), event(2)]
        result = build_device_sdt(self.snapshot(), events, worker={'state': 'running', 'sessionId': 'webcam-test'})
        pace = result['cindySenseWorkoutTracker']['paceMonitoring']
        self.assertNotEqual(pace['performanceState'], 'pace_decreasing')
