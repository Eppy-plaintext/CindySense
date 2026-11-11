import unittest
from unittest.mock import patch

import httpx

from backend import main
from backend.tinyiot import TinyIoTClient


class CindyPTTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.goal = None
        self.recommendations = []
        self.summaries = []
        self.failure = None
        self.failure_method = None

        def handle(request):
            self.calls.append(request)
            path = request.url.path
            if self.failure and path.endswith(self.failure) and (self.failure_method is None or request.method == self.failure_method):
                return httpx.Response(500, json={'m2m:dbg': 'failed'})
            if request.method == 'POST':
                content = request.read()
                import json
                cin = json.loads(content)['m2m:cin']
                self.assertEqual(request.headers['Content-Type'], 'application/json;ty=4')
                self.assertEqual(cin['cnf'], 'application/json:0')
                stored = json.loads(cin['con'])
                if path.endswith('/FitnessGoals'):
                    self.goal = stored
                elif path.endswith('/AIRecommendations'):
                    self.recommendations.append(stored)
                return httpx.Response(201, json={'m2m:cin': {'ri': 'new'}})
            if request.url.params.get('rcn') == '6':
                return httpx.Response(200, json={'m2m:rrl': {'rrf': [
                    {'typ': 4, 'val': f'TinyIoT/CindySense_AE/WorkoutSummary/{index}'}
                    for index in range(len(self.summaries) - 1, -1, -1)]}})
            if path.endswith(('/FitnessGoals', '/WorkoutSummary', '/AIRecommendations')):
                return httpx.Response(200, json={'m2m:cnt': {'cni': len(self.summaries)}})
            if path.endswith('/FitnessGoals/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': self.goal}}) if self.goal else httpx.Response(404)
            if '/WorkoutSummary/' in path:
                index = int(path.rsplit('/', 1)[-1])
                return httpx.Response(200, json={'m2m:cin': {'con': self.summaries[index], 'ct': f'20261107T12000{index}', 'ri': str(index)}})
            if path.endswith('/AIRecommendations/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': self.recommendations[-1]}}) if self.recommendations else httpx.Response(404)
            return httpx.Response(404)

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        self.client_patch = patch.object(main, 'tinyiot', TinyIoTClient('http://localhost:3000/TinyIoT', self.upstream))
        self.settings_patch = patch.object(main, 'settings', main.settings.__class__(
            cindypt_origin='pt-origin', workout_summary_resource='CindySense_AE/WorkoutSummary',
            target_plan_resource='CindyPT_AE/FitnessGoals', recommendations_resource='CindyPT_AE/AIRecommendations'))
        self.client_patch.start()
        self.settings_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        self.settings_patch.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    async def test_goal_progress_deduplication_and_stored_rule_based_advice(self):
        response = await self.api.post('/api/cindypt/goal', json={'targetRounds': 7, 'durationSeconds': 1200})
        self.assertEqual(response.status_code, 201)
        self.summaries = [
            {'workoutId': 'w1', 'rounds': 5, 'durationSeconds': 1200, 'reps': {'pullup': 25}, 'status': 'completed'},
            {'workoutId': 'w1', 'rounds': 5, 'durationSeconds': 1200, 'reps': {'pullup': 25}, 'status': 'completed'},
            {'workoutId': 'w2', 'rounds': 6, 'durationSeconds': 1180, 'reps': {'pullup': 30, 'pushup': 60, 'squat': 90}, 'status': 'completed'},
        ]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['workout']['workoutId'], 'w2')
        self.assertEqual(progress['workout']['reps']['pushup'], 60)
        self.assertEqual(progress['roundsRemaining'], 1)
        self.assertFalse(progress['goalAchieved'])
        generated = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertEqual(generated.status_code, 201)
        self.assertEqual(self.recommendations[0]['source'], 'rule-based')
        self.assertEqual(self.recommendations[0]['sourceWorkoutId'], 'w2')
        self.assertEqual((await self.api.get('/api/cindypt/recommendations')).json()['recommendations'], self.recommendations[0])
        repeated = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertTrue(repeated.json()['alreadySaved'])
        self.assertEqual(len(self.recommendations), 1)
        self.assertTrue(all(call.headers['X-M2M-Origin'] == 'pt-origin' for call in self.calls))
        self.assertFalse(any(call.url.path.endswith(('/CindyPT_AE', '/CindySense_AE')) and call.method == 'POST' for call in self.calls))

    async def test_new_completed_workout_replaces_historical_progress_and_can_generate_new_advice(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [
            {'workoutId': 'historical', 'rounds': 1, 'durationSeconds': 180, 'status': 'completed'},
            {'workoutId': 'in-progress', 'rounds': 7, 'durationSeconds': 900, 'status': 'running'},
        ]
        self.recommendations = [{'source': 'rule-based', 'sourceWorkoutId': 'historical', 'goal': self.goal}]
        before = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(before['workout']['workoutId'], 'historical')

        self.summaries.append({
            'sessionId': 'new-session', 'rounds': 6, 'durationSeconds': 1200,
            'pullups': 30, 'pushups': 60, 'squats': 90, 'status': 'completed',
            'completionReason': 'timer',
        })
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['workout']['workoutId'], 'new-session')
        self.assertEqual(progress['workout']['roundsCompleted'], 6)
        self.assertEqual(progress['roundsRemaining'], 1)
        self.assertEqual((await self.api.get('/api/cindypt/recommendations')).json()['recommendations']['sourceWorkoutId'], 'historical')

        generated = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertEqual(generated.status_code, 201)
        self.assertEqual(self.recommendations[-1]['sourceWorkoutId'], 'new-session')
        self.assertEqual(self.recommendations[-1]['roundsCompleted'], 6)
        self.assertEqual((await self.api.get('/api/cindypt/recommendations')).json()['recommendations'], self.recommendations[-1])

    async def test_current_advice_is_a_read_only_preview_for_latest_summary(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [{'sessionId': 'webcam-session', 'rounds': 2,
                           'durationSeconds': 1200, 'status': 'completed'}]
        response = await self.api.get('/api/cindypt/recommendations/current')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['recommendations']['sourceWorkoutId'], 'webcam-session')
        self.assertEqual(self.recommendations, [])

    async def test_missing_goal_or_summary_does_not_generate_data(self):
        self.assertFalse((await self.api.get('/api/cindypt/goal')).json()['available'])
        self.assertFalse((await self.api.get('/api/cindypt/recommendations')).json()['available'])
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 409)
        await self.api.post('/api/cindypt/goal', json={'targetRounds': 7, 'durationSeconds': 1200})
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertFalse(progress['available'])
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 409)
        self.assertEqual(self.recommendations, [])

    async def test_incomplete_summary_and_missing_reps(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [
            {'workoutId': 'done', 'rounds': 4, 'durationSeconds': 1300, 'status': 'completed'},
            {'workoutId': 'running', 'status': 'running'},
        ]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['workout']['workoutId'], 'done')
        self.assertIsNone(progress['workout']['reps'])
        self.assertEqual(progress['durationDifferenceSeconds'], 100)

    async def test_live_workout_summary_shape_preserves_observed_reps(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [{'rounds': 1, 'pullups': 5, 'pushups': 10, 'squats': 15,
                           'durationSeconds': 180, 'status': 'completed'}]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['workout']['reps'], {'pullup': 5, 'pushup': 10, 'squat': 15})
        self.assertEqual(progress['workout']['workoutId'], '0')
        self.assertEqual(progress['roundsRemaining'], 6)

    async def test_goal_can_carry_visible_test_label(self):
        payload = {'label': 'TEST CindyPT integration', 'targetRounds': 7, 'durationSeconds': 1200}
        response = await self.api.post('/api/cindypt/goal', json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual((await self.api.get('/api/cindypt/goal')).json()['goal']['label'], payload['label'])

    async def test_missing_status_is_not_completed(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [{'workoutId': 'unverified', 'rounds': 7, 'durationSeconds': 1200}]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertFalse(progress['available'])
        self.assertIsNone(progress['workout'])
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 409)

    async def test_upstream_failure_and_invalid_summary_do_not_write(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [{'workoutId': 'bad', 'rounds': -1, 'durationSeconds': 1200, 'status': 'completed'}]
        response = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'invalid_content')
        self.failure = '/WorkoutSummary'
        response = await self.api.get('/api/cindypt/progress')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'tinyiot_error')
        self.assertEqual(self.recommendations, [])

    async def test_invalid_goal_and_missing_container(self):
        self.assertEqual((await self.api.post('/api/cindypt/goal', json={'targetRounds': 0, 'durationSeconds': 1200})).status_code, 422)
        self.failure = '/FitnessGoals'
        self.assertEqual((await self.api.post('/api/cindypt/goal', json={'targetRounds': 7, 'durationSeconds': 1200})).status_code, 502)
        self.assertIsNone(self.goal)

    async def test_conflicting_duplicate_summary_is_rejected(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [
            {'workoutId': 'same', 'rounds': 4, 'durationSeconds': 1200, 'status': 'completed'},
            {'workoutId': 'same', 'rounds': 5, 'durationSeconds': 1200, 'status': 'completed'},
        ]
        response = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'duplicate_workout_conflict')
        self.assertEqual(self.recommendations, [])

    async def test_failed_recommendation_write_is_reported(self):
        self.goal = {'targetRounds': 7, 'durationSeconds': 1200}
        self.summaries = [{'workoutId': 'w1', 'rounds': 7, 'durationSeconds': 1190, 'status': 'completed'}]
        self.failure = '/AIRecommendations'
        self.failure_method = 'POST'
        response = await self.api.post('/api/cindypt/recommendations/generate')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'tinyiot_error')
        self.assertEqual(self.recommendations, [])
