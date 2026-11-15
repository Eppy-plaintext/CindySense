"""Mock CSE integration of callback, durable coaching, and dashboard readback."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend import main
from backend.coaching_jobs import CoachingJobs
from backend.notifications import NotificationHub
from backend.tinyiot import TinyIoTClient
from tools.workout_summary import completed_summary


SUMMARY = 'CindySense_AE/WorkoutSummary'
GOALS = 'CindyPT_AE/FitnessGoals'
RECOMMENDATIONS = 'CindyPT_AE/AIRecommendations'
TOKEN = 'i' * 40


class MockCSE:
    def __init__(self):
        self.summary = completed_summary(
            'webcam-20261111T000000Z-a1b2c3d4', SimpleNamespace(rounds=2, elapsed=1200),
            {'pullup': 10, 'pushup': 20, 'squat': 30}, 'timer')
        self.summary_name = 'webcam-summary-' + self.summary['sessionId']
        self.goal = {'targetRounds': 3, 'durationSeconds': 1200}
        self.recommendations = {}
        self.posts = 0
        self.reads = []

    def handle(self, request):
        path = request.url.path.removeprefix('/TinyIoT/')
        self.reads.append((request.method, path))
        if request.method == 'GET' and path == SUMMARY + '/' + self.summary_name:
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                  json={'m2m:cin': {'rn': self.summary_name, 'ri': 'summary-ri-1',
                                                    'con': json.dumps(self.summary)}})
        if request.method == 'GET' and path == GOALS:
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cnt': {}})
        if request.method == 'GET' and path == GOALS + '/la':
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                  json={'m2m:cin': {'ri': 'goal-ri-1', 'con': json.dumps(self.goal)}})
        if request.method == 'GET' and path == RECOMMENDATIONS:
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cnt': {}})
        if request.method == 'GET' and path == RECOMMENDATIONS + '/la' and self.recommendations:
            name, content = next(reversed(self.recommendations.items()))
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                  json={'m2m:cin': {'rn': name, 'ri': name,
                                                    'con': json.dumps(content)}})
        if request.method == 'GET' and path.startswith(RECOMMENDATIONS + '/'):
            name = path.rsplit('/', 1)[-1]
            if name in self.recommendations:
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                      json={'m2m:cin': {'rn': name, 'ri': name,
                                                        'con': json.dumps(self.recommendations[name])}})
            return httpx.Response(404, headers={'X-M2M-RSC': '4004'}, json={'m2m:dbg': 'missing'})
        if request.method == 'POST' and path == RECOMMENDATIONS:
            if request.headers.get('X-M2M-Origin') != 'CCindyPT':
                return httpx.Response(403, headers={'X-M2M-RSC': '4103'},
                                      json={'m2m:dbg': 'wrong origin'})
            cin = json.loads(request.content)['m2m:cin']
            self.posts += 1
            self.recommendations[cin['rn']] = json.loads(cin['con'])
            return httpx.Response(201, headers={'X-M2M-RSC': '2001'},
                                  json={'m2m:cin': {'rn': cin['rn']}})
        return httpx.Response(404, headers={'X-M2M-RSC': '4004'}, json={'m2m:dbg': 'missing'})


class CoachingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cse = MockCSE()
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.cse.handle))
        self.client = TinyIoTClient('http://mock/TinyIoT', self.http)
        self.jobs = CoachingJobs(Path(self.temp.name) / 'jobs.sqlite3')
        self.hub = NotificationHub()
        self.hub.subscriptions = {'summary': [SUMMARY + '/Summary_Sub']}
        self.hub.subscription_ids = {'summary': ['subscription-ri-1']}
        self.hub.on_summary = self.jobs.enqueue
        self.settings = SimpleNamespace(notification_token=TOKEN, notification_origin='TinyIoT',
                                        workout_summary_resource=SUMMARY, cindypt_origin='CCindyPT',
                                        repetition_resource='CindySense_AE/RepetitionCount',
                                        workout_origin='CDashboard',
                                        target_plan_resource=GOALS,
                                        recommendations_resource=RECOMMENDATIONS)
        counts = {'pullup': 10, 'pushup': 20, 'squat': 30}
        observed = {'availableSessions': [{'sessionId': self.cse.summary['sessionId'],
                    'exerciseCounts': counts, 'eventCount': 60, 'suitable': True,
                    'firstEventAt': '2026-11-11T00:00:00Z', 'lastEventAt': '2026-11-11T00:20:00Z',
                    'performance': None}], 'retainedInstanceCount': 60, 'exclusionReasons': {}}
        self.history_patch = patch('backend.coaching_jobs.read_fatigue_history', new=AsyncMock(return_value=observed))
        self.history_patch.start()

    async def asyncTearDown(self):
        self.history_patch.stop()
        await self.http.aclose()
        self.temp.cleanup()

    async def test_verified_notification_to_dashboard_with_duplicate_delivery(self):
        signal = {'m2m:sgn': {'sur': '/tinyiot/subscription-ri-1',
                             'nev': {'net': 3, 'rep': {'m2m:cin': {
                                 'rn': self.cse.summary_name, 'ri': 'summary-ri-1'}}}}}
        headers = {'X-M2M-RI': 'request-1', 'X-M2M-Origin': 'TinyIoT',
                   'X-M2M-RVI': '4', 'Content-Type': 'application/json'}
        listener = asyncio.Queue()
        self.hub.listeners.add(listener)
        with patch.object(main, 'notification_hub', self.hub), \
             patch.object(main, 'tinyiot', self.client), \
             patch.object(main, 'settings', self.settings):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url='http://backend') as api:
                accepted = await api.post('/api/onem2m/notify/' + TOKEN,
                                          json=signal, headers=headers)
                self.assertEqual((accepted.status_code, accepted.headers['X-M2M-RSC']), (200, '2000'))
                self.assertEqual(self.jobs.metrics(), {'pending': 1})
                self.assertEqual(self.cse.posts, 0)  # Callback did no coaching work.
                self.assertEqual((await listener.get())[0], 'summary')
                restarted = CoachingJobs(self.jobs.path)
                self.assertTrue(await restarted.process_one(self.client, self.settings, self.hub.publish))
                self.assertEqual(restarted.metrics(), {'done': 1})
                self.assertEqual(self.cse.posts, 1)
                self.assertEqual((await listener.get())[0], 'recommendation')
                advice = next(iter(self.cse.recommendations.values()))
                self.assertEqual(advice['source'], 'rule-based')
                self.assertEqual(advice['sourceWorkoutId'], self.cse.summary['sessionId'])
                self.assertEqual(advice['goalVersion'], 'goal-ri-1')
                self.assertIn(('GET', RECOMMENDATIONS + '/' + next(iter(self.cse.recommendations))), self.cse.reads)
                displayed = await api.get('/api/cindypt/recommendations')
                self.assertEqual(displayed.status_code, 200)
                self.assertEqual(displayed.json()['recommendations'], advice)
                repeated = await api.post('/api/onem2m/notify/' + TOKEN,
                                          json=signal, headers=headers)
                self.assertEqual(repeated.status_code, 200)
                self.assertFalse(await restarted.process_one(self.client, self.settings))
                self.assertEqual(self.cse.posts, 1)


if __name__ == '__main__':
    unittest.main()
