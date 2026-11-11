"""In-memory end-to-end Cindy round; never writes to a running CSE."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend import main
from backend.notifications import NotificationHub
from backend.tinyiot import TinyIoTClient
from backend.workout_snapshot import WorkoutSnapshot
from tools.webcam_tinyiot import TinyIoTClient as WebcamClient


REPS = 'CindySense_AE/RepetitionCount'
SUMMARIES = 'CindySense_AE/WorkoutSummary'
GOALS = 'CindyPT_AE/FitnessGoals'
TOKEN = 'integration-test-token-' + 'x' * 32


class MemoryCSE:
    def __init__(self):
        self.records = {REPS: [], SUMMARIES: [], GOALS: []}
        self.failed_posts = 0
        self.reads = []

    def handle(self, request):
        path = request.url.path.removeprefix('/TinyIoT/').strip('/')
        origin = request.headers.get('X-M2M-Origin')
        if request.method == 'GET':
            self.reads.append((path, origin))
            if path in self.records:
                if request.url.params.get('rcn') == '6':
                    offset = int(request.url.params.get('ofst', 0))
                    limit = int(request.url.params.get('lim', 100))
                    rows = self.records[path][::-1][offset:offset + limit]
                    return self.reply(200, {'m2m:rrl': {'rrf': [
                        {'typ': 4, 'val': 'TinyIoT/' + path + '/' + row['rn']} for row in rows]}})
                return self.reply(200, {'m2m:cnt': {'mni': 100, 'cni': len(self.records[path]), 'cbs': 0}})
            parent, _, name = path.rpartition('/')
            rows = self.records.get(parent, [])
            row = rows[-1] if name == 'la' and rows else next((item for item in rows if item['rn'] == name), None)
            return self.reply(200, {'m2m:cin': row}) if row else self.reply(404, {})
        if request.method == 'POST' and path in self.records:
            if self.failed_posts:
                self.failed_posts -= 1
                return self.reply(503, {})
            cin = json.loads(request.content)['m2m:cin']
            name = cin.get('rn', 'auto-' + str(len(self.records[path]) + 1))
            if any(item['rn'] == name for item in self.records[path]):
                return self.reply(409, {})
            row = {**cin, 'rn': name, 'ri': 'ri-' + name,
                   'ct': f'20261111T0000{len(self.records[path]):02d},000'}
            self.records[path].append(row)
            return self.reply(201, {'m2m:cin': row})
        return self.reply(404, {})

    @staticmethod
    def reply(status, body):
        return httpx.Response(status, headers={'X-M2M-RSC': {
            200: '2000', 201: '2001', 403: '4103', 404: '4004', 409: '4105', 503: '5000'}[status]}, json=body)


class RoundIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cse = MemoryCSE()
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(self.cse.handle))
        self.client = TinyIoTClient('http://mock/TinyIoT', self.upstream)
        self.publisher = WebcamClient('http://mock/TinyIoT', 'CAdmin')
        self.hub = NotificationHub()
        self.hub.subscriptions = {'repetition': [REPS + '/RepCount_Sub'],
                                  'summary': [SUMMARIES + '/Summary_Sub']}
        self.settings = SimpleNamespace(notification_token=TOKEN, notification_origin='TinyIoT',
            repetition_resource=REPS, workout_summary_resource=SUMMARIES,
            target_plan_resource=GOALS, workout_origin='CDashboard', cindypt_origin='CCindyPT')
        self.snapshot = WorkoutSnapshot()
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://app')
        self.patches = (patch.object(main, 'tinyiot', self.client),
                        patch.object(main, 'settings', self.settings),
                        patch.object(main, 'notification_hub', self.hub),
                        patch.object(main, 'workout_snapshot', self.snapshot),
                        patch.object(WebcamClient, '_request', lambda publisher, method, suffix='', body=None, ty=None:
                            self.publisher_request(publisher, method, suffix, body, ty)))
        for item in self.patches:
            item.start()
        self.cse.records[GOALS].append({'rn': 'goal', 'ri': 'ri-goal',
            'con': json.dumps({'targetRounds': 1, 'durationSeconds': 1200})})
        self.stream = (await main.integration_events()).body_iterator
        self.assertIn('reconcile', await anext(self.stream))

    async def asyncTearDown(self):
        await self.stream.aclose()
        for item in reversed(self.patches):
            item.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    def publisher_request(self, publisher, method, suffix='', body=None, ty=None):
        request = httpx.Request(method, 'http://mock/TinyIoT' + suffix,
            headers={'X-M2M-Origin': publisher.origin}, json=body)
        response = self.cse.handle(request)
        return response.status_code, response.headers['X-M2M-RSC'], response.json()

    async def deliver(self, resource, row):
        kind = 'repetition' if resource == REPS else 'summary'
        signal = {'m2m:sgn': {'sur': resource + '/' + ('RepCount_Sub' if kind == 'repetition' else 'Summary_Sub'),
            'nev': {'net': 3, 'rep': {'m2m:cin': row}}}}
        return await self.api.post('/api/onem2m/notify/' + TOKEN, json=signal,
            headers={'X-M2M-RI': str(uuid4()), 'X-M2M-Origin': 'TinyIoT', 'X-M2M-RVI': '4'})

    async def test_complete_round_storage_callbacks_sse_and_cindypt_preview(self):
        session = 'webcam-20261111T000000Z-round'
        sequence = 0
        for exercise, target in (('pullup', 5), ('pushup', 10), ('squat', 15)):
            for count in range(1, target + 1):
                sequence += 1
                event = {'eventId': str(uuid4()), 'eventSequence': sequence, 'sessionId': session,
                    'exercise': exercise, 'repetitions': count, 'round': 1,
                    'timestamp': f'2026-11-11T00:{sequence // 60:02d}:{sequence % 60:02d}Z',
                    'source': 'webcam'}
                self.assertEqual(self.publisher.publish_event(event), '2001')
                response = await self.deliver(REPS, self.cse.records[REPS][-1])
                self.assertEqual((response.status_code, response.headers['X-M2M-RSC']), (200, '2000'))
                sse = await asyncio.wait_for(anext(self.stream), 1)
                self.assertIn('event: repetition', sse)
                data = json.loads(sse.split('data: ', 1)[1])
                self.assertEqual((data['eventId'], data['eventSequence'], data['sessionId']),
                                 (event['eventId'], sequence, session))
                self.assertIn('callbackReceivedAt', data)
                self.assertIn('sseEmittedAt', data)
        self.assertEqual(len(self.cse.records[REPS]), 30)
        snapshot = (await self.api.get('/api/workout/snapshot')).json()
        self.assertEqual(snapshot['sources']['webcam']['counts'], {'pullup': 5, 'pushup': 10, 'squat': 15})
        self.assertEqual(snapshot['sources']['webcam']['completedRounds'], 1)
        self.assertEqual(snapshot['sources']['webcam']['eventCount'], 30)
        last = self.cse.records[REPS][-1]
        self.assertEqual((await self.deliver(REPS, last)).status_code, 200)
        self.assertEqual(len(self.hub.seen), 30)
        self.assertEqual(self.publisher.publish_event(event), 'verified')
        self.assertEqual(len(self.cse.records[REPS]), 30)
        summary = {'sessionId': session, 'status': 'completed', 'rounds': 1,
                   'durationSeconds': 1200, 'pullups': 5, 'pushups': 10, 'squats': 15}
        self.assertEqual(self.publisher.publish_summary(summary), 'created')
        response = await self.deliver(SUMMARIES, self.cse.records[SUMMARIES][-1])
        self.assertEqual((response.status_code, response.headers['X-M2M-RSC']), (200, '2000'))
        self.assertIn('event: summary', await asyncio.wait_for(anext(self.stream), 1))
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertTrue(progress['goalAchieved'])
        self.assertEqual(progress['workout']['roundsCompleted'], 1)
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()
        self.assertEqual(advice['recommendations']['sourceWorkoutId'], session)
        self.assertEqual(self.cse.records.get('CindyPT_AE/AIRecommendations'), None)
        self.assertEqual(self.publisher.publish_summary(summary), 'verified')
        self.assertEqual(len(self.cse.records[SUMMARIES]), 1)
        self.assertEqual((await self.deliver(SUMMARIES, self.cse.records[SUMMARIES][-1])).status_code, 200)
        self.assertTrue(next(iter(self.hub.listeners)).empty())
        metrics = (await self.api.get('/api/integration/metrics')).json()
        self.assertEqual(metrics['validatedCallbacks'], 33)
        self.assertEqual(metrics['duplicateCallbacks'], 2)
        self.assertIn((REPS + '/' + last['rn'], 'CDashboard'), self.cse.reads)
        self.assertIn((SUMMARIES + '/' + self.cse.records[SUMMARIES][-1]['rn'], 'CCindyPT'), self.cse.reads)

    async def test_outage_retry_reconciliation_and_rejected_callback(self):
        event = {'eventId': str(uuid4()), 'eventSequence': 1,
                 'sessionId': 'webcam-20261111T000000Z-retry', 'exercise': 'pullup',
                 'repetitions': 1, 'round': 1, 'timestamp': '2026-11-11T00:00:01Z', 'source': 'webcam'}
        self.cse.failed_posts = 1
        with self.assertRaises(Exception):
            self.publisher.publish_event(event)
        self.assertEqual(len(self.cse.records[REPS]), 0)
        self.assertEqual(self.publisher.publish_event(event), '2001')
        self.assertEqual((await self.api.get('/api/workout/snapshot')).json()['sources']['webcam']['counts']['pullup'], 1)
        row = self.cse.records[REPS][0]
        self.assertEqual((await self.deliver(REPS, row)).status_code, 200)
        self.assertIn('event: repetition', await asyncio.wait_for(anext(self.stream), 1))
        wrong = {**row, 'con': '{}'}
        response = await self.deliver(REPS, wrong)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.hub.seen), 1)
        self.assertEqual((await self.api.get('/api/integration/metrics')).json()['rejectedCallbacks'], 1)

    async def test_out_of_order_callbacks_use_stored_absolute_counts(self):
        session = 'webcam-20261111T000000Z-order'
        for count in (1, 2):
            event = {'eventId': str(uuid4()), 'eventSequence': count,
                     'sessionId': session, 'exercise': 'pullup', 'repetitions': count,
                     'round': 1, 'timestamp': f'2026-11-11T00:00:0{count}Z',
                     'source': 'webcam'}
            self.assertEqual(self.publisher.publish_event(event), '2001')
        newer, older = self.cse.records[REPS][1], self.cse.records[REPS][0]
        self.assertEqual((await self.deliver(REPS, newer)).status_code, 200)
        self.assertIn('event: repetition', await asyncio.wait_for(anext(self.stream), 1))
        self.assertEqual((await self.api.get('/api/workout/snapshot')).json()
                         ['sources']['webcam']['counts']['pullup'], 2)
        self.assertEqual((await self.deliver(REPS, older)).status_code, 200)
        self.assertIn('event: repetition', await asyncio.wait_for(anext(self.stream), 1))
        self.assertEqual((await self.api.get('/api/workout/snapshot')).json()
                         ['sources']['webcam']['counts']['pullup'], 2)

    async def test_sse_reconnect_reconciles_event_delivered_while_offline(self):
        await self.stream.aclose()
        self.assertEqual(len(self.hub.listeners), 0)
        event = {'eventId': str(uuid4()), 'eventSequence': 1,
                 'sessionId': 'webcam-20261111T000000Z-reconnect', 'exercise': 'pullup',
                 'repetitions': 1, 'round': 1, 'timestamp': '2026-11-11T00:00:01Z',
                 'source': 'webcam'}
        self.assertEqual(self.publisher.publish_event(event), '2001')
        self.assertEqual((await self.deliver(REPS, self.cse.records[REPS][-1])).status_code, 200)
        self.stream = (await main.integration_events()).body_iterator
        self.assertIn('event: reconcile', await anext(self.stream))
        snapshot = (await self.api.get('/api/workout/snapshot')).json()
        self.assertEqual(snapshot['sources']['webcam']['counts']['pullup'], 1)
