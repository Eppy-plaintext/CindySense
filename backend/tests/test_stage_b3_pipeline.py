"""End-to-end B3 pipeline against a disposable in-memory CSE and local SQLite."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from backend.coaching_jobs import CoachingJobs
from backend.notifications import NotificationHub
from backend.summary_reconciliation import SummaryReconciler
from backend.tinyiot import TinyIoTError
from tools.tests.test_stage_b3_integration import DisposableCSE, wait_for
from tools.webcam_tinyiot import PosePublisher, TinyIoTClient
from tools.workout_summary import SummaryPublisher, completed_summary


SESSION = 'webcam-20261118T001000Z-c1d2e3f4'
REPS = 'CindySense_AE/RepetitionCount'
SUMMARIES = 'CindySense_AE/WorkoutSummary'
SUMMARY_NAME = 'webcam-summary-' + SESSION


class BackendCSE:
    def __init__(self, store):
        self.store = store
        self.saved = {}
        self.read_origins = []

    async def retrieve(self, path, origin):
        self.read_origins.append(origin)
        if path == SUMMARIES:
            return {'m2m:cnt': {'cni': 1}}
        cin = self.store.content.get('/' + path)
        if cin is None:
            raise TinyIoTError('resource_not_found', 'missing', 404)
        return {'m2m:cin': {**cin, 'ri': 'ri-' + cin['rn']}}

    async def list_content_instances(self, path, origin, **kwargs):
        self.read_origins.append(origin)
        return [SUMMARIES + '/' + SUMMARY_NAME]

    @staticmethod
    def parse_content_instance(payload):
        cin = payload['m2m:cin']
        content = cin['con']
        return (json.loads(content) if isinstance(content, str) else content), cin

    async def latest_content(self, path, origin):
        self.read_origins.append(origin)
        return {'targetRounds': 2, 'durationSeconds': 1200}, {'ri': 'goal-ri'}

    async def create_named_recommendation(self, path, origin, key, content):
        self.saved.setdefault(key, content)
        return True


class StageB3PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DisposableCSE()
        self.client = BackendCSE(self.store)
        self.jobs = CoachingJobs(Path(self.temp.name) / 'jobs.sqlite3')
        self.settings = SimpleNamespace(
            notification_token='t' * 40, notification_origin='TinyIoT',
            repetition_resource=REPS, workout_summary_resource=SUMMARIES,
            workout_origin='CDashboard', cindypt_origin='CCindyPT',
            target_plan_resource='CindyPT_AE/FitnessGoals',
            recommendations_resource='CindyPT_AE/AIRecommendations')

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_journal_to_summary_callback_reconciliation_and_coaching(self):
        def request(client, method, suffix='', body=None, ty=None):
            return self.store.request(client, method, suffix, body, ty)
        with patch.object(TinyIoTClient, '_request', request):
            publisher = PosePublisher(event_spool=Path(self.temp.name) / 'legacy.json',
                                      session_id=SESSION, shutdown_grace=1)
            publisher.set_summary_publisher(SummaryPublisher(TinyIoTClient(), attempts=1))
            counts = {'pullup': 0, 'pushup': 0, 'squat': 0}
            try:
                for sequence, exercise in enumerate(
                        ['pullup'] * 5 + ['pushup'] * 10 + ['squat'] * 15, 1):
                    counts[exercise] += 1
                    publisher.offer_event({
                        'eventId': str(uuid4()), 'eventSequence': sequence,
                        'sessionId': SESSION, 'exercise': exercise,
                        'repetitions': counts[exercise], 'round': 1,
                        'timestamp': '2026-11-18T00:10:00+00:00', 'source': 'webcam'})
                completed = completed_summary(
                    SESSION, SimpleNamespace(rounds=1, elapsed=1200), counts, 'timer')
                self.assertEqual(publisher.complete_summary(completed, 30, timeout=12), 'delivered')
                self.assertEqual(publisher.diagnostics()['eventJournalDelivered'], 30)
            finally:
                publisher.close()
        self.assertEqual(len(self.store.posts), 31)
        hub = NotificationHub()
        hub.subscriptions = {'summary': [SUMMARIES + '/Summary_Sub']}
        hub.subscription_ids = {'summary': ['summary-sub-ri']}
        hub.on_summary = self.jobs.enqueue
        signal = {'m2m:sgn': {'sur': '/TinyIoT/summary-sub-ri', 'nev': {'net': 3,
                  'rep': {'m2m:cin': {'rn': SUMMARY_NAME, 'ri': 'ri-' + SUMMARY_NAME}}}}}
        headers = {'x-m2m-ri': 'callback-ri', 'x-m2m-origin': 'TinyIoT', 'x-m2m-rvi': '4'}
        await hub.receive(signal, headers, self.settings.notification_token,
                          self.client, self.settings)
        await hub.receive(signal, headers, self.settings.notification_token,
                          self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.assertEqual(hub.metrics()['duplicateCallbacks'], 1)
        recon = SummaryReconciler(self.jobs, enabled=True, max_pages=1)
        await recon.check_once(self.client, self.settings)
        self.assertEqual(recon.metrics()['duplicates'], 1)
        restarted_jobs = CoachingJobs(self.jobs.path)
        observed = {'availableSessions': [{'sessionId': SESSION, 'exerciseCounts': counts,
            'eventCount': 30, 'suitable': True,
            'firstEventAt': '2026-11-18T00:10:00Z',
            'lastEventAt': '2026-11-18T00:30:00Z', 'performance': None}],
            'retainedInstanceCount': 30, 'exclusionReasons': {}}
        with patch('backend.coaching_jobs.read_fatigue_history',
                   new=AsyncMock(return_value=observed)):
            self.assertTrue(await restarted_jobs.process_one(self.client, self.settings))
        self.assertEqual(restarted_jobs.metrics(), {'done': 1})
        self.assertEqual(len(self.client.saved), 1)
        self.assertFalse(await restarted_jobs.process_one(self.client, self.settings))
        self.assertEqual(len(self.client.saved), 1)
        self.assertIn('CCindyPT', self.client.read_origins)
        self.assertNotIn('CAdmin', self.client.read_origins)


if __name__ == '__main__':
    unittest.main()
