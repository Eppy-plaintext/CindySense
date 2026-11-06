import unittest
import json
from unittest.mock import patch
import os

import httpx

from backend import main
from backend.config import tinyiot_base_url
from backend.tinyiot import TinyIoTClient


class ConfigTests(unittest.TestCase):
    def test_tinyiot_base_path_keeps_required_case(self):
        with patch.dict(os.environ, {'TINYIOT_BASE_URL': 'http://localhost:3000/tinyiot/'}):
            self.assertEqual(tinyiot_base_url(), 'http://localhost:3000/TinyIoT')


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.workout_content = 11
        self.recommendation_content = {
            'roundsCompleted': 1,
            'durationSeconds': 180,
            'recommendation': 'Focus on consistent form and build endurance gradually.',
            'source': 'rule-based',
        }

        def handle(request):
            self.requests.append(request)
            path = request.url.path
            if path.endswith('/RepetitionCount') or path.endswith('/AIRecommendations'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cnt': {'rn': path.rsplit('/', 1)[-1]}})
            if path.endswith('/RepetitionCount/la'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cin': {'con': self.workout_content, 'ct': '20261027T120000'}})
            if path.endswith('/AIRecommendations/la'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cin': {'con': self.recommendation_content, 'ct': '20261027T120000'}})
            return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:cb': {}})

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        self.client_patch = patch.object(main, 'tinyiot', TinyIoTClient('http://localhost:3000/TinyIoT', self.upstream))
        self.settings_patch = patch.object(main, 'settings', main.settings.__class__(
            base_url='http://localhost:3000/TinyIoT',
            workout_origin='authorized-workout-origin',
            cindypt_origin='authorized-pt-origin',
            repetition_resource='CindySense_AE/RepetitionCount',
            recommendations_resource='CindyPT_AE/AIRecommendations',
        ))
        self.client_patch.start()
        self.settings_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        self.settings_patch.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    async def test_live_reads_verified_container_and_latest_with_required_headers(self):
        response = await self.api.get('/api/workout/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['latest_repetition_content'], 11)
        self.assertEqual(response.json()['content_kind'], 'legacy_unclassified')
        self.assertEqual(response.json()['legacy_content'], 11)
        self.assertIsNone(response.json()['structured_event'])
        self.assertNotIn('pullUps', response.json())
        self.assertNotIn('roundsCompleted', response.json())
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(self.requests[1].url.path.endswith('/RepetitionCount/la'))
        self.assertEqual(self.requests[0].headers['X-M2M-Origin'], 'authorized-workout-origin')
        self.assertEqual(self.requests[0].headers['X-M2M-RVI'], '4')
        self.assertTrue(self.requests[0].headers['X-M2M-RI'].startswith('cindysense-'))

    async def test_numeric_string_repetition_content(self):
        self.workout_content = '11'
        response = await self.api.get('/api/workout/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['latest_repetition_content'], 11)

    async def test_webcam_json_string_squat_event_is_parsed_without_round_inference(self):
        event = {
            'eventId': 'a18a9ce4-684d-45bc-944e-81dc7442828c',
            'eventSequence': 1, 'sessionId': 'webcam-20261106T120000Z',
            'exercise': 'squat', 'repetitions': 1, 'round': 1,
            'timestamp': '2026-11-06T12:00:00Z', 'source': 'webcam',
        }
        self.workout_content = json.dumps(event)
        response = await self.api.get('/api/workout/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['content_kind'], 'structured_event')
        self.assertEqual(response.json()['structured_event'], event)
        self.assertEqual(response.json()['latest_repetition_content'], event)
        self.assertNotIn('roundsCompleted', response.json())
        self.assertTrue(all('/TinyIoT/' in str(request.url) for request in self.requests))

    async def test_recommendations_use_separate_origin(self):
        response = await self.api.get('/api/cindypt/recommendations')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recommendations'], self.recommendation_content)
        self.assertEqual(self.requests[0].headers['X-M2M-Origin'], 'authorized-pt-origin')

    async def test_json_string_recommendation_content(self):
        self.recommendation_content = '{"roundsCompleted":1,"durationSeconds":180,"recommendation":"Build endurance.","source":"rule-based"}'
        response = await self.api.get('/api/cindypt/recommendations')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recommendations']['roundsCompleted'], 1)

    async def test_history_explicitly_unavailable(self):
        response = await self.api.get('/api/workout/history')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sessions'], [])
        self.assertFalse(response.json()['available'])

    async def test_health_separates_backend_from_resource_access(self):
        response = await self.api.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['backend']['available'])
        self.assertTrue(response.json()['tinyiot']['reachable'])
        self.assertTrue(response.json()['tinyiot']['resource_verified'])

    async def test_cors_allows_both_local_vite_origins(self):
        for origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
            response = await self.api.options('/api/workout/live', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})
            self.assertEqual(response.headers['access-control-allow-origin'], origin)


class ErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_forbidden_missing_and_invalid_content_are_not_success(self):
        async def request_with(response):
            upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
            client = TinyIoTClient('http://localhost:3000/TinyIoT', upstream)
            with patch.object(main, 'tinyiot', client), patch.object(main, 'settings', main.settings.__class__(workout_origin='AE')):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver') as api:
                    result = await api.get('/api/workout/live')
            await upstream.aclose()
            return result

        for upstream_response, status, code in [
            (httpx.Response(403, headers={'X-M2M-RSC': '4103'}, json={'m2m:dbg': 'forbidden'}), 403, 'tinyiot_forbidden'),
            (httpx.Response(404, headers={'X-M2M-RSC': '4004'}, json={'m2m:dbg': 'not found'}), 404, 'resource_not_found'),
            (httpx.Response(200, headers={'X-M2M-RSC': '2000'}, text='not-json'), 502, 'invalid_tinyiot_response'),
        ]:
            result = await request_with(upstream_response)
            self.assertEqual(result.status_code, status)
            self.assertEqual(result.json()['detail']['code'], code)

    async def test_connection_failure_and_unparseable_repetitions(self):
        async def check(handler, expected_status, expected_code):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
                client = TinyIoTClient('http://localhost:3000/TinyIoT', upstream)
                with patch.object(main, 'tinyiot', client), patch.object(main, 'settings', main.settings.__class__(workout_origin='AE')):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver') as api:
                        result = await api.get('/api/workout/live')
            self.assertEqual(result.status_code, expected_status)
            self.assertEqual(result.json()['detail']['code'], expected_code)

        def disconnected(request):
            raise httpx.ConnectError('connection refused', request=request)

        def invalid_content(request):
            if request.url.path.endswith('/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': 'not a repetition payload'}})
            return httpx.Response(200, json={'m2m:cnt': {}})

        await check(disconnected, 503, 'tinyiot_unreachable')
        await check(invalid_content, 502, 'invalid_content')


if __name__ == '__main__':
    unittest.main()
