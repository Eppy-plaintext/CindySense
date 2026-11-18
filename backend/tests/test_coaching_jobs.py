import tempfile
import asyncio
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from backend.coaching_jobs import CoachingJobs
from backend.config import Settings
from backend.tinyiot import TinyIoTError


class FakeClient:
    def __init__(self):
        self.summary = {'sessionId': 'session-1', 'status': 'completed', 'rounds': 2,
                        'durationSeconds': 1200, 'pullups': 10, 'pushups': 20, 'squats': 30,
                        'source': 'webcam', 'completionReason': 'timer'}
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
        async def observed(*args, **kwargs):
            summary = self.client.summary
            counts = {'pullup': summary.get('pullups', 0), 'pushup': summary.get('pushups', 0),
                      'squat': summary.get('squats', 0)}
            return {'availableSessions': [{'sessionId': summary.get('sessionId'),
                'exerciseCounts': counts, 'eventCount': sum(counts.values()),
                'firstEventAt': '2026-11-16T00:00:00Z', 'lastEventAt': '2026-11-16T00:20:00Z',
                'suitable': all(counts.values()), 'performance': None}],
                'retainedInstanceCount': sum(counts.values()), 'exclusionReasons': {}}
        self.history_patch = patch('backend.coaching_jobs.read_fatigue_history', new=AsyncMock(side_effect=observed))
        self.history_patch.start()

    async def asyncTearDown(self):
        self.history_patch.stop()
        self.temp.cleanup()

    async def test_short_verified_stop_does_not_publish_advice(self):
        self.client.summary.update({'durationSeconds': 40, 'completionReason': 'explicit_stop'})
        self.jobs.enqueue('summary', 'summary-ri')
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'done': 1})
        self.assertEqual(self.client.saved, {})

    async def test_mismatched_webcam_workout_identity_is_discarded(self):
        self.client.summary['workoutId'] = 'another-session'
        self.jobs.enqueue('summary', 'summary-ri')
        await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.jobs.metrics(), {'done': 1})
        self.assertEqual(self.client.saved, {})

    async def test_full_summary_without_matching_events_does_not_publish_advice(self):
        self.client.summary['pushups'] = 19
        self.jobs.enqueue('summary', 'summary-ri')
        async def mismatched(*args, **kwargs):
            return {'availableSessions': [{'sessionId': 'session-1', 'exerciseCounts':
                {'pullup': 10, 'pushup': 20, 'squat': 30}, 'eventCount': 60, 'suitable': True,
                'firstEventAt': '2026-11-16T00:00:00Z', 'lastEventAt': '2026-11-16T00:20:00Z',
                'performance': None}], 'retainedInstanceCount': 60, 'exclusionReasons': {}}
        with patch('backend.coaching_jobs.read_fatigue_history', new=AsyncMock(side_effect=mismatched)):
            await self.jobs.process_one(self.client, self.settings)
        self.assertEqual(self.client.saved, {})

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
                        {'status': 'completed', 'rounds': -1, 'durationSeconds': 30},
                        {'status': 'completed', 'rounds': 1, 'durationSeconds': 30,
                         'synthetic': True, 'source': 'webcam'}):
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

    async def test_worker_recovers_after_local_database_failure(self):
        recovered = asyncio.Event()
        calls = 0

        async def process(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError('disk temporarily unavailable')
            recovered.set()
            return False

        with patch.object(self.jobs, 'process_one', side_effect=process):
            task = asyncio.create_task(self.jobs.run(self.client, self.settings))
            try:
                await asyncio.wait_for(recovered.wait(), timeout=4)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertGreaterEqual(calls, 2)
