import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.webcam_tinyiot import (RetryableSummaryError, TinyIoTClient,
                                  TinyIoTConnectionError, TinyIoTError)
from tools.workout_summary import SummaryPublisher, completed_summary


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TinyIoTClient()
        self.summary = completed_summary(
            'webcam-20261107T120000Z-12345678',
            SimpleNamespace(rounds=2, elapsed=1200),
            {'pullup': 10, 'pushup': 20, 'squat': 30}, 'timer')
        self.name = 'webcam-summary-' + self.summary['sessionId']
        self.child = '/CindySense_AE/WorkoutSummary/' + self.name
        self.stored = {'m2m:cin': {'rn': self.name, 'con': json.dumps(self.summary)}}

    def test_timer_and_early_stop_payloads(self):
        self.assertEqual(self.summary['status'], 'completed')
        self.assertEqual(self.summary['durationSeconds'], 1200)
        self.assertEqual((self.summary['pullups'], self.summary['pushups'],
                          self.summary['squats'], self.summary['rounds']), (10, 20, 30, 2))
        early = completed_summary('webcam-20261107T120100Z-abcdef12', SimpleNamespace(rounds=1, elapsed=75.2),
                                  {'pullup': 6, 'pushup': 10, 'squat': 15}, 'explicit_stop')
        self.assertEqual((early['status'], early['completionReason'], early['durationSeconds']),
                         ('completed', 'explicit_stop', 76))
        with self.assertRaises(ValueError):
            completed_summary('interrupted', SimpleNamespace(rounds=1, elapsed=75),
                              {'pullup': 5, 'pushup': 10, 'squat': 15}, 'quit')

    def test_rejects_unrelated_identity_and_inconsistent_rounds(self):
        totals = {'pullup': 5, 'pushup': 10, 'squat': 15}
        workout = SimpleNamespace(rounds=1, elapsed=81.5)
        with self.assertRaisesRegex(ValueError, 'original webcam session ID'):
            completed_summary('final-summary-097c0172', workout, totals, 'explicit_stop')
        with self.assertRaisesRegex(ValueError, 'Completed rounds exceed'):
            completed_summary(self.summary['sessionId'], workout,
                              {**totals, 'squat': 14}, 'explicit_stop')
        with self.assertRaisesRegex(ValueError, 'valid elapsed duration'):
            completed_summary(self.summary['sessionId'],
                              SimpleNamespace(rounds=1, elapsed=float('nan')), totals, 'timer')

    def test_create_verifies_named_instance_and_skips_duplicate(self):
        calls = []
        responses = iter([(200, '2000', {'m2m:cnt': {}}), (404, '4004', {}),
                          (201, '2001', {}), (200, '2000', self.stored)])
        def request(method, suffix='', body=None, ty=None):
            calls.append((method, suffix, body, ty))
            return next(responses)
        self.client._request = request
        self.assertEqual(self.client.publish_summary(self.summary), 'created')
        self.assertEqual(calls[2][0:2], ('POST', '/CindySense_AE/WorkoutSummary'))
        self.assertEqual(calls[2][3], 4)
        self.assertEqual(calls[2][2]['m2m:cin']['rn'], self.name)
        self.assertEqual(json.loads(calls[2][2]['m2m:cin']['con']), self.summary)

        self.client._request = lambda method, suffix='', body=None, ty=None: (
            (200, '2000', {'m2m:cnt': {}}) if suffix == '/CindySense_AE/WorkoutSummary'
            else (200, '2000', self.stored))
        self.assertEqual(self.client.publish_summary(self.summary), 'verified')

    def test_ambiguous_post_confirms_exact_readback(self):
        responses = iter([(200, '2000', {'m2m:cnt': {}}), (404, '4004', {}),
                          (503, '5000', {}), (200, '2000', self.stored)])
        self.client._request = lambda *args, **kwargs: next(responses)
        self.assertEqual(self.client.publish_summary(self.summary), 'verified')

    def test_mismatched_duplicate_is_rejected(self):
        wrong = {'m2m:cin': {'rn': self.name, 'con': json.dumps({**self.summary, 'rounds': 3})}}
        self.client._request = lambda method, suffix='', body=None, ty=None: (
            (200, '2000', {'m2m:cnt': {}}) if suffix == '/CindySense_AE/WorkoutSummary'
            else (200, '2000', wrong))
        with self.assertRaisesRegex(TinyIoTError, 'mismatch'):
            self.client.publish_summary(self.summary)

    def test_transient_connection_error_retries_and_in_memory_duplicate_skips(self):
        self.client.publish_summary = Mock(side_effect=[TinyIoTConnectionError('offline'), 'created'])
        publisher = SummaryPublisher(self.client, attempts=3, retry_delay=0)
        self.assertEqual(publisher.publish(self.summary), 'created')
        self.assertEqual(self.client.publish_summary.call_count, 2)
        self.assertEqual(publisher.publish(self.summary), 'already_persisted')
        self.assertEqual(self.client.publish_summary.call_count, 2)
        with self.assertRaisesRegex(TinyIoTError, 'different results'):
            publisher.publish({**self.summary, 'rounds': 3})

    def test_unconfirmed_retry_is_bounded_and_permission_failure_is_not_retried(self):
        self.client.publish_summary = Mock(side_effect=RetryableSummaryError('server unavailable'))
        publisher = SummaryPublisher(self.client, attempts=3, retry_delay=0)
        with self.assertRaisesRegex(TinyIoTError, 'not confirmed after 3 attempts'):
            publisher.publish(self.summary)
        self.assertEqual(self.client.publish_summary.call_count, 3)
        self.client.publish_summary = Mock(side_effect=TinyIoTError('HTTP 403'))
        with self.assertRaisesRegex(TinyIoTError, 'HTTP 403'):
            publisher.publish(self.summary)
        self.assertEqual(self.client.publish_summary.call_count, 1)

    def test_missing_container_never_posts(self):
        self.client._request = Mock(return_value=(404, '4004', {}))
        with self.assertRaises(TinyIoTError):
            self.client.publish_summary(self.summary)
        self.client._request.assert_called_once_with('GET', '/CindySense_AE/WorkoutSummary')


if __name__ == '__main__':
    unittest.main()
