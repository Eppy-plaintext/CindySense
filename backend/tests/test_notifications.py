import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.notifications import NotificationHub
from backend.tinyiot import TinyIoTClient
from backend import main


TOKEN = 'x' * 40
URL = 'http://172.20.0.1:8000/api/onem2m/notify/' + TOKEN
REPS = 'CindySense_AE/RepetitionCount'
SUMMARIES = 'CindySense_AE/WorkoutSummary'


class CSE:
    def __init__(self):
        self.subscriptions = {}
        self.posts = []
        self.puts = []
        self.event = {'eventId': str(uuid4()), 'sessionId': 'webcam-20261111T000000Z-a',
                      'exercise': 'pullup', 'repetitions': 1, 'round': 1,
                      'timestamp': '2026-11-11T00:00:01Z', 'source': 'webcam'}
        self.summary = {'sessionId': self.event['sessionId'], 'status': 'completed',
                        'rounds': 0, 'durationSeconds': 1200, 'pullups': 1,
                        'pushups': 0, 'squats': 0}

    def handle(self, request):
        path = request.url.path.removeprefix('/TinyIoT/')
        if request.method == 'PUT':
            self.puts.append(path)
            self.subscriptions[path].update(json.loads(request.content)['m2m:sub'])
            return httpx.Response(200, headers={'X-M2M-RSC': '2004'},
                                  json={'m2m:sub': self.subscriptions[path]})
        if request.method == 'POST':
            self.posts.append(path)
            sub = json.loads(request.content)['m2m:sub']
            self.subscriptions[path + '/' + sub['rn']] = sub
            return httpx.Response(201, headers={'X-M2M-RSC': '2001'}, json={'m2m:sub': sub})
        if request.url.params.get('rcn') == '4':
            return httpx.Response(200, json={'m2m:cnt': {'m2m:sub': [
                sub for key, sub in self.subscriptions.items() if key.startswith(path + '/')]}})
        if request.url.params.get('rcn') == '6':
            return httpx.Response(200, json={'m2m:rrl': {'rrf': [
                {'typ': 23, 'val': 'TinyIoT/' + key} for key in self.subscriptions if key.startswith(path + '/')]}})
        if path in self.subscriptions:
            return httpx.Response(200, json={'m2m:sub': self.subscriptions[path]})
        if path.endswith('/event'):
            return httpx.Response(200, json={'m2m:cin': {'rn': 'event', 'ri': 'event-ri', 'con': json.dumps(self.event)}})
        if path.endswith('/summary'):
            return httpx.Response(200, json={'m2m:cin': {'rn': 'summary', 'ri': 'summary-ri', 'con': json.dumps(self.summary)}})
        return httpx.Response(200, json={'m2m:cnt': {}})


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cse = CSE()
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.cse.handle))
        self.client = TinyIoTClient('http://localhost:3000/TinyIoT', self.http)
        self.settings = SimpleNamespace(notification_url=URL, notification_token=TOKEN,
                                        subscription_origin='CAdmin', notification_origin='TinyIoT',
                                        repetition_resource=REPS, workout_summary_resource=SUMMARIES,
                                        workout_origin='CDashboard', cindypt_origin='CCindyPT')
        self.hub = NotificationHub()

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_create_and_reuse_subscriptions(self):
        await self.hub.ensure(self.client, self.settings)
        self.assertEqual(self.cse.posts, [REPS, SUMMARIES])
        await self.hub.ensure(self.client, self.settings)
        self.assertEqual(self.cse.posts, [REPS, SUMMARIES])
        self.assertEqual(self.cse.subscriptions[REPS + '/cindysense-fastapi-repetitions']['enc']['net'], [3])

    async def test_repairs_existing_named_subscriptions_without_duplicates(self):
        for resource, name in ((REPS, 'RepCount_Sub'), (SUMMARIES, 'Summary_Sub')):
            self.cse.subscriptions[resource + '/' + name] = {
                'rn': name, 'nu': ['http://127.0.0.1:5000/old'],
                'enc': {'net': [3]}, 'nct': 1}
        result = await self.hub.ensure(self.client, self.settings)
        self.assertEqual(self.cse.posts, [])
        self.assertEqual(self.cse.puts, [REPS + '/RepCount_Sub', SUMMARIES + '/Summary_Sub'])
        self.assertEqual(result['repetition'], [REPS + '/RepCount_Sub'])
        self.assertEqual(self.cse.subscriptions[REPS + '/RepCount_Sub']['nu'], [URL])
        await self.hub.ensure(self.client, self.settings)
        self.assertEqual(len(self.cse.puts), 2)

    async def test_verification_valid_delivery_retry_and_tampering(self):
        await self.hub.ensure(self.client, self.settings)
        headers = {'x-m2m-ri': 'request-1', 'x-m2m-origin': 'TinyIoT', 'x-m2m-rvi': '4'}
        self.assertEqual(await self.hub.receive({'m2m:sgn': {'vrq': True}}, headers, TOKEN,
                                                self.client, self.settings), ('verification', 'request-1'))
        listener = __import__('asyncio').Queue()
        self.hub.listeners.add(listener)
        signal = {'m2m:sgn': {'sur': '/TinyIoT/' + REPS + '/cindysense-fastapi-repetitions',
                             'nev': {'net': 3, 'rep': {'m2m:cin': {'rn': 'event', 'ri': 'event-ri',
                                                                  'con': json.dumps(self.cse.event)}}}}}
        self.assertEqual((await self.hub.receive(signal, headers, TOKEN, self.client, self.settings))[0], 'repetition')
        kind, data = listener.get_nowait()
        self.assertEqual(kind, 'repetition')
        self.assertEqual(data['eventId'], self.cse.event['eventId'])
        self.assertEqual(data['sessionId'], self.cse.event['sessionId'])
        self.assertIsNone(data['eventSequence'])
        self.assertIn('callbackReceivedAt', data)
        await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)
        self.assertTrue(listener.empty())
        with self.assertRaises(ValueError):
            await self.hub.receive(signal, headers, 'wrong', self.client, self.settings)
        with self.assertRaises(ValueError):
            await self.hub.receive(signal, {**headers, 'x-m2m-origin': 'attacker'}, TOKEN,
                                   self.client, self.settings)
        signal['m2m:sgn']['nev']['rep']['m2m:cin']['con'] = '{}'
        with self.assertRaises(ValueError):
            await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)

    async def test_tinyiot_subscription_ri_notification_reference_is_accepted(self):
        await self.hub.ensure(self.client, self.settings)
        listener = __import__('asyncio').Queue()
        self.hub.listeners.add(listener)
        signal = {'m2m:sgn': {'sur': '/tinyiot/rep-ri',
                             'nev': {'net': 3, 'rep': {'m2m:cin': {'rn': 'event', 'ri': 'event-ri',
                                                                  'con': json.dumps(self.cse.event)}}}}}
        self.hub.subscription_ids['repetition'] = ['rep-ri']
        headers = {'x-m2m-ri': 'request-ri', 'x-m2m-origin': 'TinyIoT', 'x-m2m-rvi': '4'}
        self.assertEqual((await self.hub.receive(signal, headers, TOKEN, self.client, self.settings))[0], 'repetition')
        self.assertEqual(listener.get_nowait()[0], 'repetition')

    async def test_tinyiot_rvi_three_is_accepted(self):
        await self.hub.ensure(self.client, self.settings)
        listener = __import__('asyncio').Queue()
        self.hub.listeners.add(listener)
        signal = {'m2m:sgn': {'sur': '/tinyiot/rep-ri-3',
                             'nev': {'net': 3, 'rep': {'m2m:cin': {'rn': 'event', 'ri': 'event-ri',
                                                                  'con': json.dumps(self.cse.event)}}}}}
        self.hub.subscription_ids['repetition'] = ['rep-ri-3']
        headers = {'x-m2m-ri': 'request-ri-3', 'x-m2m-origin': 'TinyIoT', 'x-m2m-rvi': '3'}
        self.assertEqual((await self.hub.receive(signal, headers, TOKEN, self.client, self.settings))[0], 'repetition')
        self.assertEqual(listener.get_nowait()[0], 'repetition')
        signal['m2m:sgn']['sur'] = 'CindySense_AE/RepetitionCount/unknown-subscription'
        with self.assertRaises(ValueError):
            await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)

    async def test_completed_summary_notifies_once(self):
        await self.hub.ensure(self.client, self.settings)
        listener = __import__('asyncio').Queue()
        self.hub.listeners.add(listener)
        signal = {'m2m:sgn': {'sur': SUMMARIES + '/cindysense-fastapi-summaries',
                             'nev': {'net': 3, 'rep': {'m2m:cin': {'rn': 'summary', 'ri': 'summary-ri'}}}}}
        headers = {'x-m2m-ri': 'request-2', 'x-m2m-origin': 'TinyIoT', 'x-m2m-rvi': '4'}
        await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)
        self.assertEqual(listener.get_nowait(), ('summary', {}))
        await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)
        self.assertTrue(listener.empty())
        self.cse.summary['status'] = 'running'
        self.hub.seen.clear()
        with self.assertRaises(ValueError):
            await self.hub.receive(signal, headers, TOKEN, self.client, self.settings)
        self.assertTrue(listener.empty())

    async def test_sse_queue_overflow_is_reported_separately(self):
        listener = __import__('asyncio').Queue(maxsize=1)
        self.hub.listeners.add(listener)
        self.hub.publish('repetition', {'eventId': 'first'})
        self.hub.publish('repetition', {'eventId': 'second'})
        self.assertEqual(listener.get_nowait()[1]['eventId'], 'first')
        self.assertEqual(self.hub.metrics()['sseQueueDrops'], 1)

    async def test_http_acknowledgment_and_rejected_notification(self):
        await self.hub.ensure(self.client, self.settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url='http://testserver') as api:
            with patch.object(main, 'notification_hub', self.hub), \
                 patch.object(main, 'tinyiot', self.client), \
                 patch.object(main, 'settings', self.settings):
                headers = {'X-M2M-RI': 'verification-1', 'X-M2M-Origin': 'TinyIoT',
                           'X-M2M-RVI': '4', 'Content-Type': 'application/json'}
                verified = await api.post('/api/onem2m/notify/' + TOKEN,
                                          json={'m2m:sgn': {'vrq': True}}, headers=headers)
                self.assertEqual(verified.status_code, 200)
                self.assertEqual(verified.headers['X-M2M-RSC'], '2000')
                self.assertEqual(verified.json()['m2m:rsp']['rqi'], 'verification-1')
                denied = await api.post('/api/onem2m/notify/' + 'wrong',
                                        json={'m2m:sgn': {'vrq': True}}, headers=headers)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(self.hub.seen, set())

    async def test_group_helpers_use_typed_create_and_read_only_fanout(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.method == 'POST':
                self.assertEqual(request.headers['Content-Type'], 'application/json;ty=9')
                body = json.loads(request.content)
                self.assertEqual(body['m2m:grp']['mt'], 3)
                return httpx.Response(201, headers={'X-M2M-RSC': '2001'},
                                      json={'m2m:grp': body['m2m:grp']})
            if request.url.path.endswith('/fopt'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                      json={'m2m:agr': {'m2m:rsp': []}})
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                  json={'m2m:grp': {'mid': [REPS, SUMMARIES]}})

        client = TinyIoTClient('http://localhost:3000/TinyIoT',
                               httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            created = await client.create_group('CindySense_AE', 'CAdmin', 'WorkoutMonitoring_GRP',
                                                [REPS, SUMMARIES])
            self.assertEqual(created['m2m:grp']['mid'], ['TinyIoT/' + REPS, 'TinyIoT/' + SUMMARIES])
            self.assertEqual(await client.group_members('CindySense_AE/WorkoutMonitoring_GRP', 'CAdmin'),
                             [REPS, SUMMARIES])
            await client.fanout_retrieve('CindySense_AE/WorkoutMonitoring_GRP', 'CAdmin')
            self.assertEqual([request.method for request in requests], ['POST', 'GET', 'GET'])
            self.assertTrue(requests[-1].url.path.endswith('/fopt'))
        finally:
            await client._client.aclose()


if __name__ == '__main__':
    unittest.main()
