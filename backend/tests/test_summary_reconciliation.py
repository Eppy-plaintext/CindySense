"""Read-only missed-callback recovery with a disposable CSE and job database."""

import sqlite3
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.coaching_jobs import CoachingJobs
from backend.summary_reconciliation import SummaryReconciler
from backend.tinyiot import TinyIoTError

RESOURCE = 'CindySense_AE/WorkoutSummary'
SESSION = 'webcam-20261118T000000Z-12345678'
REF = RESOURCE + '/webcam-summary-' + SESSION


class FakeCSE:
    def __init__(self):
        self.content = {'sessionId': SESSION, 'status': 'completed', 'source': 'webcam',
                        'completionReason': 'timer', 'rounds': 1, 'durationSeconds': 1200,
                        'pullups': 5, 'pushups': 10, 'squats': 15}
        self.instance = {'rn': REF.rsplit('/', 1)[-1], 'ri': 'summary-ri'}
        self.refs = [REF]
        self.origins = []
        self.fail = False

    async def retrieve(self, path, origin):
        self.origins.append(origin)
        if self.fail:
            raise TinyIoTError('tinyiot_unreachable', 'offline', 503)
        return {'m2m:cnt': {'cni': len(self.refs)}} if path == RESOURCE else {
            'm2m:cin': {**self.instance, 'con': self.content}}

    async def list_content_instances(self, path, origin, **kwargs):
        self.origins.append(origin)
        self.options = kwargs
        checkpoint = kwargs.get('stop_at')
        if checkpoint and self.refs and self.refs[0] in checkpoint:
            return [self.refs[0]]
        return self.refs

    def parse_content_instance(self, payload):
        cin = payload['m2m:cin']
        return cin['con'], cin


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.jobs = CoachingJobs(Path(self.temp.name) / 'jobs.sqlite3')
        self.client = FakeCSE()
        self.settings = SimpleNamespace(cindypt_origin='CCindyPT', workout_summary_resource=RESOURCE)
        self.reconciler = SummaryReconciler(self.jobs, enabled=True, max_pages=2)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_missed_callback_is_enqueued_once_with_read_origin_and_checkpoint(self):
        status = await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(status['enqueued'], 1)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.assertEqual(self.client.origins, ['CCindyPT'] * 3)
        self.assertEqual(self.client.options['max_pages'], 2)
        restarted = SummaryReconciler(self.jobs, enabled=True)
        await restarted.check_once(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.assertEqual(restarted.metrics()['enqueued'], 0)

    async def test_callback_and_reconciliation_use_same_idempotent_identity(self):
        self.assertTrue(self.jobs.enqueue(REF, 'summary-ri'))
        await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(self.reconciler.metrics()['duplicates'], 1)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.assertFalse(self.jobs.enqueue(REF, 'summary-ri'))

    async def test_concurrent_callback_and_scan_leave_one_job(self):
        await asyncio.gather(
            asyncio.to_thread(self.jobs.enqueue, REF, 'summary-ri'),
            self.reconciler.check_once(self.client, self.settings))
        self.assertEqual(self.jobs.metrics(), {'pending': 1})

    async def test_invalid_or_mismatched_identity_is_not_queued(self):
        self.client.instance['rn'] = 'different-name'
        await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(self.reconciler.metrics()['invalid'], 1)
        self.assertEqual(self.jobs.metrics(), {})

    async def test_outage_does_not_advance_checkpoint_and_can_retry(self):
        self.client.fail = True
        status = await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(status['lastError'], 'tinyiot_unreachable')
        self.assertIsNone(self.reconciler._checkpoint(RESOURCE))
        self.client.fail = False
        status = await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(status['enqueued'], 1)

    async def test_sqlite_failure_is_reported_without_checkpoint(self):
        with patch.object(self.jobs, '_db', side_effect=sqlite3.OperationalError('disk full')):
            status = await self.reconciler.check_once(self.client, self.settings)
        self.assertEqual(status['lastError'], 'local_database_error')
        self.assertIsNone(self.reconciler._checkpoint(RESOURCE))
        self.assertEqual(self.jobs.metrics(), {})

    async def test_disabled_reconciliation_does_not_query_cse(self):
        disabled = SummaryReconciler(self.jobs, enabled=False)
        await disabled.check_once(self.client, self.settings)
        self.assertEqual(self.client.origins, [])


if __name__ == '__main__':
    unittest.main()
