import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx

from backend import main
from backend.tinyiot import TinyIoTClient
from backend.weekly_plan import PlanPreferences, generate_weekly_plan


GOAL = {'targetRounds': 7, 'durationSeconds': 1200, 'label': None}
PREFERENCES = {'timezone': 'Asia/Seoul', 'trainingDays': [0, 2, 4], 'experienceLevel': 'beginner'}


class PlannerRulesTests(unittest.TestCase):
    def test_webcam_provenance_requires_a_webcam_session_identity(self):
        prefs = PlanPreferences.model_validate(PREFERENCES)
        summary = {'workoutId': 'final-summary-097c0172', 'roundsCompleted': 1,
                   'durationSeconds': 1200, 'source': 'webcam'}
        plan = generate_weekly_plan(GOAL, [summary], prefs, date(2026, 11, 15))
        self.assertEqual(plan['workoutProvenance'], 'unverified')
        self.assertEqual(plan['sourceWorkoutId'], summary['workoutId'])
        self.assertEqual([day['suggestedRounds'] for day in plan['days'] if day['type'] == 'workout'], [1, 1, 1])
        webcam_named = {**summary, 'workoutId': 'webcam-20261111T102705Z-f7a4b355'}
        self.assertEqual(generate_weekly_plan(GOAL, [webcam_named], prefs, date(2026, 11, 15))
                         ['workoutProvenance'], 'webcam')

    def test_new_user_starter_plan_and_calendar(self):
        prefs = PlanPreferences.model_validate(PREFERENCES)
        plan = generate_weekly_plan(GOAL, [], prefs, today=date(2026, 11, 7))
        self.assertEqual(plan['weekStart'], '2026-11-09')
        self.assertEqual(len(plan['days']), 7)
        self.assertEqual([day['type'] for day in plan['days']],
                         ['workout', 'recovery', 'workout', 'recovery', 'workout', 'recovery', 'recovery'])
        self.assertEqual([day['suggestedRounds'] for day in plan['days'] if day['type'] == 'workout'], [1, 1, 1])
        self.assertTrue(plan['limitedInformation'])
        self.assertEqual(plan['workoutProvenance'], 'no-workout-history')
        self.assertTrue(all(day['date'] >= '2026-11-07' for day in plan['days']))
        self.assertEqual(plan, generate_weekly_plan(GOAL, [], prefs, today=date(2026, 11, 7)))

    def test_below_goal_uses_history_without_jumping_to_ultimate_target(self):
        history = [{'workoutId': 'new', 'roundsCompleted': 4, 'durationSeconds': 1150, 'source': 'webcam'},
                   {'workoutId': 'old', 'roundsCompleted': 3, 'durationSeconds': 1200, 'source': 'webcam'}]
        plan = generate_weekly_plan(GOAL, history, PlanPreferences.model_validate(PREFERENCES), date(2026, 11, 9))
        self.assertEqual([day['suggestedRounds'] for day in plan['days'] if day['type'] == 'workout'], [3, 4, 4])
        self.assertFalse(plan['limitedInformation'])
        self.assertNotEqual(plan['planId'], generate_weekly_plan(GOAL, history[:1],
                            PlanPreferences.model_validate(PREFERENCES), date(2026, 11, 9))['planId'])

    def test_achieved_goal_repeats_and_consecutive_days_stay_light(self):
        history = [{'workoutId': 'met', 'roundsCompleted': 7, 'durationSeconds': 1190, 'source': 'webcam'}]
        prefs = PlanPreferences.model_validate({**PREFERENCES, 'trainingDays': [0, 1, 3, 5]})
        plan = generate_weekly_plan(GOAL, history, prefs, date(2026, 11, 9))
        self.assertEqual([day['suggestedRounds'] for day in plan['days'] if day['type'] == 'workout'], [7, 7, 7, 7])
        self.assertIn('consecutive', plan['days'][1]['reason'])
        self.assertEqual(plan['days'][2]['type'], 'recovery')

    def test_near_goal_does_not_prescribe_ultimate_target_next(self):
        history = [{'workoutId': 'near', 'roundsCompleted': 6, 'durationSeconds': 1200}]
        prefs = PlanPreferences.model_validate({**PREFERENCES, 'experienceLevel': 'intermediate'})
        plan = generate_weekly_plan(GOAL, history, prefs, date(2026, 11, 9))
        self.assertTrue(all(day['suggestedRounds'] <= 6 for day in plan['days']))

    def test_invalid_preferences_and_monday_today(self):
        from pydantic import ValidationError
        for value in ({**PREFERENCES, 'trainingDays': [0, 0]},
                      {**PREFERENCES, 'trainingDays': [7]},
                      {**PREFERENCES, 'timezone': 'Not/A_Timezone'}):
            with self.assertRaises(ValidationError):
                PlanPreferences.model_validate(value)
        plan = generate_weekly_plan(GOAL, [], PlanPreferences.model_validate(PREFERENCES), date(2026, 11, 9))
        self.assertEqual(plan['weekStart'], '2026-11-09')


class PlanApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.goal = GOAL.copy()
        self.summaries = []
        self.records = {name: {} for name in ('TrainingPlans', 'WorkoutSchedule', 'RecoveryRecommendations')}
        self.calls = []
        self.fail_read = None
        self.fail_write = None

        def handle(request):
            self.calls.append(request)
            path = request.url.path
            method = request.method
            if self.fail_read and method == 'GET' and self.fail_read in path:
                return httpx.Response(503, json={'error': 'offline'})
            if self.fail_write and method == 'POST' and self.fail_write in path:
                return httpx.Response(503, json={'error': 'offline'})
            if method == 'POST':
                name = path.rsplit('/', 1)[-1]
                if name not in self.records:
                    return httpx.Response(404)
                cin = request.content and json.loads(request.content)['m2m:cin']
                self.records[name][cin['rn']] = json.loads(cin['con'])
                return httpx.Response(201, headers={'X-M2M-RSC': '2001'}, json={'m2m:cin': {'rn': cin['rn']}})
            if request.url.params.get('rcn') == '6' and path.endswith('/WorkoutSummary'):
                return httpx.Response(200, json={'m2m:rrl': {'rrf': [
                    {'typ': 4, 'val': f'TinyIoT/CindySense_AE/WorkoutSummary/{i}'}
                    for i in range(len(self.summaries) - 1, -1, -1)]}})
            if path.endswith('/FitnessGoals/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': self.goal}}) if self.goal else httpx.Response(404)
            if '/WorkoutSummary/' in path:
                index = int(path.rsplit('/', 1)[-1])
                return httpx.Response(200, json={'m2m:cin': {'con': self.summaries[index], 'ri': str(index), 'ct': f'20261107T12000{index}'}})
            if path.endswith('/FitnessGoals'):
                return httpx.Response(200, json={'m2m:cnt': {}})
            if path.endswith('/WorkoutSummary'):
                return httpx.Response(200, json={'m2m:cnt': {'cni': len(self.summaries)}})
            for name, records in self.records.items():
                if path.endswith('/' + name):
                    return httpx.Response(200, json={'m2m:cnt': {'cni': len(records)}})
                if path.endswith('/' + name + '/la'):
                    if not records:
                        return httpx.Response(404)
                    key = list(records)[-1]
                    return httpx.Response(200, json={'m2m:cin': {'rn': key, 'con': records[key]}})
                if '/' + name + '/' in path:
                    key = path.rsplit('/', 1)[-1]
                    return httpx.Response(200, json={'m2m:cin': {'rn': key, 'con': records[key]}}) if key in records else httpx.Response(404)
            return httpx.Response(404)

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        self.client_patch = patch.object(main, 'tinyiot', TinyIoTClient('http://localhost:3000/TinyIoT', self.upstream))
        self.settings_patch = patch.object(main, 'settings', main.settings.__class__(cindypt_origin='pt-origin'))
        self.client_patch.start()
        self.settings_patch.start()
        self.history_patch = patch.object(main, 'fatigue_history_cache')
        self.history_patch.start()
        main.fatigue_history_cache.read = AsyncMock(return_value={
            'availableSessions': [], 'retainedInstanceCount': 0, 'exclusionReasons': {}})

    async def asyncTearDown(self):
        self.history_patch.stop()
        self.client_patch.stop()
        self.settings_patch.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    async def preview(self):
        return await self.api.post('/api/cindypt/plans/preview', json=PREFERENCES)

    async def test_preview_has_no_writes_and_new_user_is_limited(self):
        response = await self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['plan']['limitedInformation'])
        self.assertEqual(response.json()['plan']['sourceWorkoutId'], None)
        self.assertTrue(all(call.method == 'GET' for call in self.calls))
        self.assertFalse((await self.api.get('/api/cindypt/plans')).json()['available'])
        self.assertFalse((await self.api.get('/api/cindypt/recovery')).json()['available'])

    async def test_save_is_explicit_idempotent_and_retrievable(self):
        self.summaries = [{'sessionId': 'webcam-new', 'status': 'completed', 'source': 'webcam',
                           'rounds': 3, 'durationSeconds': 1100, 'pullups': 15, 'pushups': 30, 'squats': 45}]
        plan = (await self.preview()).json()['plan']
        self.assertIsNone(plan['sourceWorkoutId'])
        self.assertEqual(sum(len(records) for records in self.records.values()), 0)
        request = {'preferences': PREFERENCES, 'planId': plan['planId']}
        first = await self.api.post('/api/cindypt/plans', json=request)
        self.assertEqual(first.status_code, 201)
        self.assertFalse(first.json()['alreadySaved'])
        self.assertEqual([len(records) for records in self.records.values()], [1, 1, 1])
        self.assertEqual((await self.api.get('/api/cindypt/plans')).json()['plan']['planId'], plan['planId'])
        self.assertEqual((await self.api.get('/api/cindypt/recovery')).json()['recovery']['planId'], plan['planId'])
        again = await self.api.post('/api/cindypt/plans', json=request)
        self.assertTrue(again.json()['alreadySaved'])
        self.assertEqual(len([call for call in self.calls if call.method == 'POST']), 3)
        writes = [call for call in self.calls if call.method == 'POST']
        discovery_reads = [call for call in self.calls
                           if call.method == 'GET' and call.url.params.get('fu') == '1']
        operation_reads = [call for call in self.calls if call.method == 'GET' and call not in discovery_reads]
        self.assertTrue(all(call.headers['X-M2M-Origin'] == 'pt-origin' for call in writes))
        self.assertTrue(discovery_reads)
        self.assertTrue(all(call.headers['X-M2M-Origin'] == main.settings.discovery_origin
                            for call in discovery_reads))
        self.assertTrue(all(call.headers['X-M2M-Origin'] == 'pt-origin' for call in operation_reads))

    async def test_stale_preview_and_missing_goal_do_not_write(self):
        plan = (await self.preview()).json()['plan']
        self.goal = {**GOAL, 'targetRounds': 8}
        response = await self.api.post('/api/cindypt/plans', json={'preferences': PREFERENCES, 'planId': plan['planId']})
        self.assertEqual(response.status_code, 409)
        self.goal = None
        self.assertEqual((await self.preview()).status_code, 409)
        self.assertFalse(any(call.method == 'POST' for call in self.calls))

    async def test_malformed_summary_duplicate_and_failed_read(self):
        self.summaries = [{'status': 'completed', 'rounds': -1, 'durationSeconds': 1200}]
        self.assertEqual((await self.preview()).status_code, 502)
        self.summaries = [{'workoutId': 'same', 'status': 'completed', 'rounds': 2, 'durationSeconds': 1200},
                          {'workoutId': 'same', 'status': 'completed', 'rounds': 3, 'durationSeconds': 1200}]
        self.assertEqual((await self.preview()).json()['detail']['code'], 'duplicate_workout_conflict')
        self.fail_read = 'WorkoutSummary'
        self.assertEqual((await self.preview()).status_code, 502)
        self.assertFalse(any(call.method == 'POST' for call in self.calls))

    async def test_identical_duplicate_workouts_count_once(self):
        summary = {'workoutId': 'same', 'status': 'completed', 'rounds': 3, 'durationSeconds': 1200}
        self.summaries = [summary, summary.copy()]
        plan = (await self.preview()).json()['plan']
        self.assertEqual(plan['completedWorkoutCount'], 0)
        self.assertTrue(plan['limitedInformation'])

    async def test_failed_write_can_resume_without_duplicate_training_plan(self):
        plan = (await self.preview()).json()['plan']
        request = {'preferences': PREFERENCES, 'planId': plan['planId']}
        self.fail_write = 'WorkoutSchedule'
        response = await self.api.post('/api/cindypt/plans', json=request)
        self.assertEqual(response.status_code, 502)
        self.assertEqual([len(records) for records in self.records.values()], [1, 0, 0])
        self.assertTrue((await self.api.get('/api/cindypt/plans')).json()['incomplete'])
        self.fail_write = None
        self.assertEqual((await self.api.post('/api/cindypt/plans', json=request)).status_code, 201)
        self.assertEqual([len(records) for records in self.records.values()], [1, 1, 1])
        self.assertEqual(len([call for call in self.calls if call.method == 'POST' and '/TrainingPlans' in call.url.path]), 1)


if __name__ == '__main__':
    unittest.main()
