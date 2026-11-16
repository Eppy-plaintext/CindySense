import unittest
import json
from unittest.mock import AsyncMock, patch
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
            if path.endswith('/WorkoutPerformance_TS') and request.url.params.get('fu') == '1':
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                      json={'m2m:uril': ['TinyIoT/CindySense_AE/WorkoutPerformance_TS/workout_test']})
            if path.endswith('/WorkoutPerformance_TS'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={'m2m:ts': {'ty': 29}})
            if path.endswith('/WorkoutPerformance_TS/workout_test'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                      json={'m2m:tsi': {'dgt': '20261111T120000',
                                                        'con': '{"workoutId":"session-1","rounds":1}'}})
            if request.url.params.get('fu') == '1':
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'},
                                      json={'m2m:uril': [
                                          'TinyIoT/CindySense_AE/RepetitionCount',
                                          'TinyIoT/CindySense_AE/WorkoutSummary']})
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
            discovery_origin='authorized-discovery-origin',
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

    async def test_workout_timeseries_history_is_read_only_and_scoped(self):
        response = await self.api.get('/api/onem2m/workout-timeseries')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['workouts'][0]['content']['workoutId'], 'session-1')
        self.assertEqual([request.method for request in self.requests], ['GET', 'GET', 'GET'])
        self.assertTrue(all(request.headers['X-M2M-Origin'] == 'CDashboard'
                            for request in self.requests))

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

    async def test_history_uses_validated_retained_records(self):
        observed = {'retainedInstanceCount': 1, 'exclusionReasons': {},
                    'selectedSessionId': 'webcam-one', 'selectionError': None,
                    'availableSessions': [{'sessionId': 'webcam-one',
                                           'exerciseCounts': {'pullup': 5, 'pushup': 10, 'squat': 15},
                                           'lastEventAt': '2026-11-16T00:00:00+00:00',
                                           'performance': {'state': 'stable_performance'}}]}
        with patch.object(main.fatigue_history_cache, 'read', AsyncMock(return_value=observed)), \
             patch.object(main, 'read_summaries', AsyncMock(return_value=[])), \
             patch.object(main, 'read_goal', AsyncMock(return_value=None)):
            response = await self.api.get('/api/workout/history')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sessions'][0]['status'], 'no_verified_summary')
        self.assertFalse(response.json()['sessions'][0]['comparable'])
        self.assertTrue(response.json()['available'])
        with patch.object(main.fatigue_history_cache, 'read', AsyncMock(return_value=observed)):
            performance = await self.api.get('/api/workout/performance?sessionId=webcam-one')
        self.assertEqual(performance.json()['performance']['state'], 'stable_performance')

    async def test_health_separates_backend_from_resource_access(self):
        response = await self.api.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['backend']['available'])
        self.assertTrue(response.json()['tinyiot']['reachable'])
        self.assertTrue(response.json()['tinyiot']['resource_verified'])

    async def test_sdt_device_is_read_only_and_uses_existing_sources(self):
        snapshot = {'sources': {'webcam': {'sessionId': 'webcam-test', 'counts': {'squat': 1},
                                           'completedRounds': 0, 'startAt': '2026-11-16T00:00:00+00:00'}}}
        with patch.object(main.workout_snapshot, 'read', AsyncMock(return_value=snapshot)), \
             patch.object(main.workout_snapshot, 'validated_events', return_value=[]), \
             patch.object(main, 'read_goal', AsyncMock(return_value={'targetRounds': 3})), \
             patch.object(main.tinyiot, 'latest_content', AsyncMock(return_value=({'recommendation': 'Keep form.'}, {}))):
            response = await self.api.get('/api/sdt/device')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['available'])
        self.assertFalse(body['nativeOneM2MResource'])
        self.assertEqual(body['cindySenseWorkoutTracker']['coaching']['targetRounds'], 3)

    async def test_fl_endpoints_are_read_only_and_consistent(self):
        result = {'status': 'trained', 'modelVersion': 'fedavg-logistic-v1',
                  'provenance': 'real_csv_recordings_single_user', 'roundMetrics': [{}, {}],
                  'trainSampleCount': 20, 'validationSampleCount': 5,
                  'simulation': 'Three sequential virtual clients on one machine; not three real users.'}
        with patch.object(main, 'read_result', return_value=result):
            status = await self.api.get('/api/fl/status')
            results = await self.api.get('/api/fl/results')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['roundsCompleted'], 2)
        self.assertEqual(results.status_code, 200)
        self.assertEqual(results.json()['modelVersion'], 'fedavg-logistic-v1')
        self.assertTrue(all(request.method == 'GET' for request in self.requests))

    async def test_fl_status_is_explicit_when_no_offline_run_exists(self):
        with patch.object(main, 'read_result', return_value=None):
            response = await self.api.get('/api/fl/status')
        self.assertEqual(response.json()['status'], 'not_trained')
        self.assertFalse(response.json()['available'])

    async def test_discovery_uses_onem2m_filter_usage_and_returns_authorized_containers(self):
        response = await self.api.get('/api/onem2m/discovery')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_type'], 3)
        self.assertEqual(response.json()['resources'], [
            'TinyIoT/CindySense_AE/RepetitionCount',
            'TinyIoT/CindySense_AE/WorkoutSummary'])
        request = self.requests[0]
        self.assertEqual(request.url.params['fu'], '1')
        self.assertEqual(request.url.params['rcn'], '11')
        self.assertEqual(request.url.params['ty'], '3')
        self.assertEqual(request.headers['X-M2M-Origin'], 'authorized-discovery-origin')

    async def test_cors_allows_local_vite_origins_for_reads_and_webcam_preflight(self):
        for port in (5173, 5174):
            for host in ('localhost', '127.0.0.1'):
                origin = f'http://{host}:{port}'
                for path in ('/api/health', '/api/workout/live', '/api/workout/webcam-worker'):
                    response = await self.api.get(path, headers={'Origin': origin})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['access-control-allow-origin'], origin)
                for path in ('/api/workout/webcam-worker/start', '/api/workout/webcam-worker/stop'):
                    response = await self.api.options(path, headers={
                        'Origin': origin, 'Access-Control-Request-Method': 'POST',
                        'Access-Control-Request-Headers': 'content-type',
                    })
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['access-control-allow-origin'], origin)
                    self.assertIn('POST', response.headers['access-control-allow-methods'])

    async def test_cors_does_not_allow_unrelated_origins(self):
        origin = 'http://unrelated.example:5174'
        response = await self.api.get('/api/health', headers={'Origin': origin})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('access-control-allow-origin', response.headers)
        preflight = await self.api.options('/api/workout/webcam-worker/start', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'POST',
        })
        self.assertEqual(preflight.status_code, 400)
        self.assertNotIn('access-control-allow-origin', preflight.headers)


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
