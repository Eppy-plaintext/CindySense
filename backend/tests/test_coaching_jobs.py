import tempfile
import unittest
from pathlib import Path

from backend.coaching_jobs import CoachingJobs
from backend.config import Settings
from backend.tinyiot import TinyIoTError


class FakeClient:
    def __init__(self):
        self.summary = {'sessionId': 'session-1', 'status': 'completed', 'rounds': 2,
                        'durationSeconds': 1200, 'pullups': 10, 'pushups': 20, 'squats': 30}
        self.goal = {'targetRounds': 3, 'durationSeconds': 1200}
        self.saved = {}
        self.fail = False
        self.identity = 'summary-ri'

    async def retrieve(self, path, origin):
        if self.fail:
            raise TinyIoTError('tinyiot_unreachable', 'offline', 503)
        return {'m2m:cin': {'ri': self.identity, 'con': self.summary}}

    def parse_content_instance(self, payload):
        cin = payload['m2m:cin']
        return cin['con'], cin

    async def latest_content(self, path, origin):
        if self.goal is None:
            raise TinyIoTError('resource_not_found', 'no goal', 404)
        return self.goal, {'ri': 'goal-ri'}

    async def create_named_recommendation(self, path, origin, key, content):
        self.saved.setdefault(key, content)
        return True


class CoachingJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'jobs.sqlite3'
        self.jobs = CoachingJobs(self.path)
        self.client = FakeClient()
        self.settings = Settings(cindypt_origin='CCindyPT')

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_success_duplicate_and_restart(self):
        self.jobs.enqueue('CindySense_AE/WorkoutSummary/summary', 'summary-ri')
        self.jobs.enqueue('CindySense_AE/WorkoutSummary/summary', 'summary-ri')
        events = []
        await self.jobs.process_one(self.client, self.settings, lambda *args: events.append(args))
        self.assertEqual(self.jobs.metrics(), {'done': 1})
        self.assertEqual(len(self.client.saved), 1)
        self.assertEqual(next(iter(self.client.saved.values()))['source'], 'rule-based')
        self.assertEqual(events[0][0], 'recommendation')
        self.client.identity = 'second-cin-ri'
        self.jobs.enqueue('CindySense_AE/WorkoutSummary/second', 'second-cin-ri')
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(len(self.client.saved), 1)
        restarted = CoachingJobs(self.path)
        restarted.enqueue('CindySense_AE/WorkoutSummary/summary', 'summary-ri')
        self.assertFalse(await restarted.process_one(self.client, self.settings))

    async def test_interrupted_and_malformed_are_discarded(self):
        for summary in ({'status': 'interrupted', 'rounds': 1, 'durationSeconds': 30},
                        {'status': 'completed', 'rounds': -1, 'durationSeconds': 30}):
            self.client.summary = summary
            self.jobs.enqueue('summary', 'summary-ri')
            await self.jobs.process_one(self.client, self.settings)
            self.assertEqual(self.client.saved, {})
            with self.jobs._db() as db:
                db.execute('DELETE FROM jobs')

    async def test_missing_goal_and_temporary_failure_remain_pending(self):
        self.jobs.enqueue('summary', 'summary-ri')
        self.client.goal = None
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.client.goal = {'targetRounds': 3, 'durationSeconds': 1200}
        self.client.fail = True
        with self.jobs._db() as db:
            db.execute('UPDATE jobs SET due=0')
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'pending': 1})
        self.client.fail = False
        with self.jobs._db() as db:
            db.execute('UPDATE jobs SET due=0')
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'done': 1})
