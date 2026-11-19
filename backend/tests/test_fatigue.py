import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.events import RepetitionEvent
from backend.fatigue import estimate_fatigue


BASE = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def events(intervals, exercise='squat', session='webcam-test', start=BASE, round_number=1):
    result = []
    current = start
    for sequence, interval in enumerate(intervals, 1):
        current += timedelta(seconds=interval)
        result.append(RepetitionEvent(
            eventId=str(uuid4()), eventSequence=sequence, sessionId=session,
            exercise=exercise, repetitions=sequence, round=round_number,
            timestamp=current.isoformat(), source='webcam'))
    return result


def result_for(items, exercise='squat'):
    return next(item for item in items if item['exercise'] == exercise)


class FatigueTests(unittest.TestCase):
    def test_stable_cadence_is_normal(self):
        result = result_for(estimate_fatigue(events([2] * 8)))
        self.assertEqual(result['fatigueLevel'], 'normal')
        self.assertAlmostEqual(result['baselinePaceSecondsPerRep'], 2.0)
        self.assertEqual(result['contributingSignals'], [])

    def test_gradual_slowdown_is_fatigue_signal(self):
        result = result_for(estimate_fatigue(events([2, 2, 2, 2, 2, 3, 3, 3, 3, 3])))
        self.assertIn(result['fatigueLevel'], ('possible_fatigue', 'elevated_fatigue'))
        self.assertIn('pace_slowdown', result['contributingSignals'])

    def test_long_pause_is_signal_but_not_tracking_loss(self):
        result = result_for(estimate_fatigue(events([2, 2, 2, 2, 2, 12, 2, 2, 2, 2])))
        self.assertIn('pause_duration_increase', result['contributingSignals'])
        self.assertTrue(any(item['reason'] == 'possible_pause' for item in result['excludedIntervals']))

    def test_round_transition_is_excluded(self):
        first = events([2] * 5, round_number=1)
        second = events([2] * 5, start=first[-1].timestamp + timedelta(seconds=20), round_number=2)
        all_events = [event.model_copy(update={'event_sequence': index,
                                               'repetitions': index})
                      for index, event in enumerate(first + second, 1)]
        result = result_for(estimate_fatigue(all_events))
        self.assertTrue(any(item['reason'] == 'exercise_transition_or_round_boundary'
                            for item in result['excludedIntervals']))
        self.assertNotIn('pause_duration_increase', result['contributingSignals'])

    def test_session_change_is_unknown(self):
        items = events([2] * 5) + events([2] * 5, session='webcam-other', start=BASE + timedelta(seconds=20))
        self.assertTrue(all(item['fatigueLevel'] == 'unknown' for item in estimate_fatigue(items)))
        self.assertEqual(result_for(estimate_fatigue(items))['reasonUnknown'], 'session_identity_changed')

    def test_out_of_order_sequence_is_unknown(self):
        items = events([2] * 5)
        items[3] = items[3].model_copy(update={'event_sequence': 2})
        self.assertEqual(result_for(estimate_fatigue(items))['reasonUnknown'],
                         'event_sequence_reset_or_out_of_order')

    def test_missing_timestamp_is_unknown(self):
        items = events([2] * 5)
        items[2] = items[2].model_construct(**{**items[2].__dict__, 'timestamp': None})
        self.assertEqual(result_for(estimate_fatigue(items))['reasonUnknown'], 'missing_or_invalid_timestamp')

    def test_insufficient_data_is_unknown(self):
        result = result_for(estimate_fatigue(events([2] * 4)))
        self.assertEqual(result['fatigueLevel'], 'unknown')
        self.assertEqual(result['reasonUnknown'], 'insufficient_valid_repetitions')

    def test_tracking_loss_is_unknown(self):
        items = events([2] * 6)
        quality = {str(items[3].event_id): 0.2}
        result = result_for(estimate_fatigue(items, quality_by_event_id=quality))
        self.assertEqual(result['fatigueLevel'], 'unknown')
        self.assertEqual(result['reasonUnknown'], 'tracking_quality_insufficient')

    def test_unverified_detector_quality_cannot_produce_fatigue_alert(self):
        items = events([2, 2, 2, 2, 2, 12, 3, 3, 3, 3])
        result = result_for(estimate_fatigue(items, require_quality=True))
        self.assertEqual(result['fatigueLevel'], 'unknown')
        self.assertEqual(result['reasonUnknown'], 'detector_quality_unverified')

    def test_complete_event_quality_can_enable_estimator_but_degraded_or_missing_cannot(self):
        items = events([2] * 9)
        quality = {str(item.event_id): True for item in items}
        valid = result_for(estimate_fatigue(items, quality_by_event_id=quality, require_quality=True))
        self.assertEqual(valid['fatigueLevel'], 'normal')
        quality[str(items[3].event_id)] = False
        degraded = result_for(estimate_fatigue(items, quality_by_event_id=quality, require_quality=True))
        self.assertEqual(degraded['reasonUnknown'], 'tracking_quality_insufficient')
        del quality[str(items[3].event_id)]
        missing = result_for(estimate_fatigue(items, quality_by_event_id=quality, require_quality=True))
        self.assertEqual(missing['reasonUnknown'], 'detector_quality_unverified')

    def test_reset_repetition_count_is_unknown(self):
        items = events([2] * 5)
        items[-1] = items[-1].model_copy(update={'repetitions': 1})
        result = result_for(estimate_fatigue(items))
        self.assertEqual(result['fatigueLevel'], 'unknown')
        self.assertEqual(result['reasonUnknown'], 'non_monotonic_repetition_or_timestamp')


if __name__ == '__main__':
    unittest.main()
