import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.fatigue_publication import (
    FatiguePublicationError, FatiguePublicationJobs, FatiguePublicationService,
    build_fatigue_analysis, deterministic_analysis_name,
)
from backend.tinyiot import TinyIoTError


def result(level='possible_fatigue', session='webcam-live'):
    return {'sessionId': session, 'exercise': 'pullup', 'fatigueScore': 25.0,
            'fatigueLevel': level, 'contributingSignals': ['pace_slowdown']}


def window():
    return {'windowId': 'session-exercise-v1', 'sourceEventSequence': [1, 2, 3, 4, 5],
            'firstEventSequence': 1, 'lastEventSequence': 5}


QUALITY = {'dataSufficient': True, 'validEventCount': 15, 'excludedEventCount': 0}


class FakeClient:
    def __init__(self):
        self.contents = {}
        self.failures = []
        self.posts = 0

    async def create_named_verified_content(self, resource, origin, name, content):
        self.posts += 1
        if self.failures:
            raise self.failures.pop(0)
        if name in self.contents:
            if self.contents[name] != content:
                raise TinyIoTError('content_conflict', 'conflict', 409)
            return False
        self.contents[name] = content
        return True


class FatiguePublicationTests(unittest.IsolatedAsyncioTestCase):
    def make_analysis(self):
        return build_fatigue_analysis(result(), window(), QUALITY,
                                      live_session_id='webcam-live', live_session_verified=True,
                                      generated_at=datetime(2026, 11, 14, tzinfo=timezone.utc))

    def test_schema_and_deterministic_identity(self):
        analysis = self.make_analysis()
        self.assertEqual(analysis.analysis_id,
                         deterministic_analysis_name('webcam-live', 'pullup', 'session-exercise-v1', '9B'))
        self.assertEqual(analysis.content()['schemaVersion'], 1)
        self.assertIn('not medically validated fatigue detection.', analysis.content()['medicalDisclaimer'])

    def test_eligibility_rejects_unknown_historical_and_unverified(self):
        for kwargs in ({'live_session_verified': False}, {'historical': True}, {'live_session_id': 'other'}):
            with self.assertRaises(FatiguePublicationError):
                build_fatigue_analysis(result(), window(), QUALITY,
                                       live_session_id=kwargs.pop('live_session_id', 'webcam-live'),
                                       live_session_verified=kwargs.pop('live_session_verified', True),
                                       **kwargs)
        with self.assertRaises(FatiguePublicationError):
            build_fatigue_analysis(result('unknown'), window(), QUALITY,
                                   live_session_id='webcam-live', live_session_verified=True)

    async def test_duplicate_suppression_and_conflict(self):
        client = FakeClient()
        service = FatiguePublicationService(client, 'CindyPT_AE/FatigueAnalysis', 'CCindyPT', retry_delay=0)
        analysis = self.make_analysis()
        first = await service.publish(analysis)
        second = await service.publish(analysis)
        self.assertTrue(first['created'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(client.posts, 2)
        client.contents[analysis.analysis_id]['score'] = 99.0
        with self.assertRaises(TinyIoTError):
            await service.publish(analysis)

    async def test_timeout_retry_and_readback_failure(self):
        client = FakeClient()
        client.failures = [TinyIoTError('tinyiot_unreachable', 'offline', 503)]
        service = FatiguePublicationService(client, 'CindyPT_AE/FatigueAnalysis', 'CCindyPT', retry_delay=0)
        response = await service.publish(self.make_analysis())
        self.assertEqual(response['attempts'], 2)
        client.failures = [TinyIoTError('content_unconfirmed', 'no readback', 502)] * 3
        with self.assertRaises(TinyIoTError):
            await service.publish(self.make_analysis())

    async def test_restart_recovery_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fatigue.sqlite3'
            analysis = self.make_analysis()
            queue = FatiguePublicationJobs(path)
            self.assertTrue(queue.enqueue(analysis))
            self.assertFalse(queue.enqueue(analysis))
            restarted_queue = FatiguePublicationJobs(path)
            client = FakeClient()
            service = FatiguePublicationService(client, 'CindyPT_AE/FatigueAnalysis', 'CCindyPT', retry_delay=0)
            self.assertTrue(await restarted_queue.process_one(service))
            self.assertIsNone(restarted_queue.pending())


if __name__ == '__main__':
    unittest.main()
