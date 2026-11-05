import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from backend import main
from backend.events import RepetitionEvent, classify_repetition_content, deduplicate_and_order
from backend.tinyiot import TinyIoTClient


def sample_event(**changes):
    value = {
        'eventId': str(uuid4()), 'sessionId': 'session-1', 'exercise': 'pullup',
        'repetitions': 3, 'round': 1, 'timestamp': '2026-10-27T12:00:00Z',
        'source': 'simulator',
    }
    value.update(changes)
    return value


class SchemaTests(unittest.TestCase):
    def test_valid_event_and_absolute_count(self):
        event = RepetitionEvent.model_validate(sample_event())
        self.assertEqual(event.content()['repetitions'], 3)
        self.assertEqual(classify_repetition_content(event.content())[0], 'structured_event')

    def test_optional_device_event_sequence_round_trips(self):
        event = RepetitionEvent.model_validate(sample_event(eventSequence=7, source='mpu6050'))
        self.assertEqual(event.content()['eventSequence'], 7)

    def test_webcam_event_source_is_supported(self):
        event = RepetitionEvent.model_validate(sample_event(source='webcam'))
        self.assertEqual(event.content()['source'], 'webcam')

    def test_rejects_invalid_exercise_count_round_timestamp_and_extra_fields(self):
        for change in (
            {'exercise': 'burpee'}, {'repetitions': -1}, {'repetitions': True},
            {'round': 0}, {'round': 1.5}, {'timestamp': '2026-10-27T12:00:00'},
            {'timestamp': 'yesterday'}, {'unexpected': 'field'},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                RepetitionEvent.model_validate(sample_event(**change))

    def test_legacy_numeric_is_unclassified_and_invalid_content_rejected(self):
        self.assertEqual(classify_repetition_content(11), ('legacy_unclassified', None, 11))
        self.assertEqual(classify_repetition_content(11.5), ('legacy_unclassified', None, 11.5))
        for content in (True, [], 'eleven', {'exercise': 'pullup'}, float('nan')):
            with self.subTest(content=content), self.assertRaises(ValueError):
                classify_repetition_content(content)

    def test_deduplication_and_timestamp_order(self):
        early = RepetitionEvent.model_validate(sample_event(timestamp='2026-10-27T12:00:00Z'))
        late = RepetitionEvent.model_validate(sample_event(timestamp='2026-10-27T12:00:01Z'))
        self.assertEqual(deduplicate_and_order([late, early, late]), [early, late])
        with self.assertRaises(ValueError):
            deduplicate_and_order([early, early.model_copy(update={'repetitions': 4})])


class EventAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.records = {}
        self.requests = []
        self.event = sample_event()

        def handle(request):
            self.requests.append(request)
            name = request.url.path.rsplit('/', 1)[-1]
            if request.method == 'GET' and name in self.records:
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cin': {'con': json.dumps(self.records[name])}})
            if request.method == 'GET' and name.startswith('sim-'):
                return httpx.Response(404, headers={'X-M2M-RSC': '4004'}, json={})
            if request.method == 'POST':
                body = json.loads(request.content)['m2m:cin']
                if body['rn'] in self.records:
                    return httpx.Response(409, headers={'X-M2M-RSC': '4105'}, json={})
                self.records[body['rn']] = body['con']
                return httpx.Response(201, headers={'X-M2M-RSC': '2001'}, json={'m2m:cin': body})
            if name == 'RepetitionCount':
                return httpx.Response(200, json={'m2m:cnt': {}})
            if name == 'la':
                return httpx.Response(200, json={'m2m:cin': {'con': self.event}})
            return httpx.Response(200, json={'m2m:cb': {}})

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        self.client_patch = patch.object(main, 'tinyiot', TinyIoTClient('http://localhost:3000/TinyIoT', self.upstream))
        self.settings_patch = patch.object(main, 'settings', main.settings.__class__(
            workout_origin='reader', simulation_origin='writer',
            simulation_token='a' * 32, simulation_writes_enabled=False,
        ))
        self.client_patch.start()
        self.settings_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        self.settings_patch.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    async def test_structured_live_keeps_original_field(self):
        response = await self.api.get('/api/workout/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['content_kind'], 'structured_event')
        self.assertEqual(response.json()['latest_repetition_content'], self.event)
        self.assertEqual(response.json()['structured_event']['exercise'], 'pullup')
        self.assertIsNone(response.json()['legacy_content'])

    async def test_write_disabled_does_not_contact_tinyiot(self):
        response = await self.api.post('/api/dev/workout/events', json=self.event, headers={'X-Dev-Simulation-Token': 'a' * 32})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail']['code'], 'simulation_disabled')
        self.assertEqual(self.requests, [])

    async def test_write_requires_token_and_simulator_source(self):
        with patch.object(main, 'settings', main.settings.__class__(simulation_writes_enabled=True, simulation_origin='writer', simulation_token='a' * 32)):
            denied = await self.api.post('/api/dev/workout/events', json=self.event)
            self.assertEqual(denied.status_code, 403)
            wrong_source = await self.api.post('/api/dev/workout/events', json={**self.event, 'source': 'mpu6050'}, headers={'X-Dev-Simulation-Token': 'a' * 32})
            self.assertEqual(wrong_source.status_code, 422)
        self.assertEqual(self.requests, [])

    async def test_idempotent_replay_and_changed_payload_conflict(self):
        with patch.object(main, 'settings', main.settings.__class__(simulation_writes_enabled=True, simulation_origin='writer', simulation_token='a' * 32)):
            headers = {'X-Dev-Simulation-Token': 'a' * 32}
            first = await self.api.post('/api/dev/workout/events', json=self.event, headers=headers)
            replay = await self.api.post('/api/dev/workout/events', json=self.event, headers=headers)
            changed = await self.api.post('/api/dev/workout/events', json={**self.event, 'repetitions': 4}, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['created'])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()['duplicate'])
        self.assertEqual(changed.status_code, 409)
        posts = [request for request in self.requests if request.method == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].headers['X-M2M-Origin'], 'writer')
        self.assertEqual(posts[0].headers['X-M2M-RVI'], '4')

    async def test_malformed_upstream_event_is_not_success(self):
        self.event = {'eventId': str(uuid4()), 'exercise': 'unknown'}
        response = await self.api.get('/api/workout/live')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'invalid_content')


if __name__ == '__main__':
    unittest.main()
