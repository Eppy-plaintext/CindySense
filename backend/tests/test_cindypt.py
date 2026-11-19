import unittest
from unittest.mock import AsyncMock, patch
import httpx
from backend import main
from backend.tinyiot import TinyIoTClient


class CindyPTTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.goal = {'targetRounds': 5, 'durationSeconds': 1200}
        self.summaries = []
        self.sessions = []
        self.saved = []
        self.named = {}

        def handle(request):
            path = request.url.path
            if request.method == 'POST' and path.endswith('/AIRecommendations'):
                import json
                cin = json.loads(request.content)['m2m:cin']
                stored = json.loads(cin['con'])
                self.saved.append(stored)
                self.named[cin['rn']] = stored
                return httpx.Response(201, json={'m2m:cin': {'ri': 'saved'}})
            if request.url.params.get('rcn') == '6':
                return httpx.Response(200, json={'m2m:rrl': {'rrf': [
                    {'typ': 4, 'val': f'TinyIoT/CindySense_AE/WorkoutSummary/{i}'}
                    for i in range(len(self.summaries))]}})
            if path.endswith(('/FitnessGoals', '/WorkoutSummary', '/AIRecommendations')):
                return httpx.Response(200, json={'m2m:cnt': {'cni': len(self.summaries)}})
            if path.endswith('/FitnessGoals/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': self.goal, 'ri': 'goal-1'}})
            if '/WorkoutSummary/' in path:
                i = int(path.rsplit('/', 1)[-1])
                return httpx.Response(200, json={'m2m:cin': {'con': self.summaries[i], 'ri': str(i),
                                                             'ct': f'20261116T12000{i}'}})
            if path.endswith('/AIRecommendations/la'):
                return httpx.Response(200, json={'m2m:cin': {'con': self.saved[-1]}}) if self.saved else httpx.Response(404)
            if '/AIRecommendations/' in path:
                name = path.rsplit('/', 1)[-1]
                return httpx.Response(200, json={'m2m:cin': {'rn': name, 'con': self.named[name]}}) if name in self.named else httpx.Response(404)
            return httpx.Response(404)

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver')
        self.patches = [patch.object(main, 'tinyiot', TinyIoTClient('http://mock/TinyIoT', self.upstream)),
                        patch.object(main, 'fatigue_history_cache'),
                        patch.object(main, 'settings', main.settings.__class__(
                            cindypt_origin='pt-origin', workout_summary_resource='CindySense_AE/WorkoutSummary',
                            target_plan_resource='CindyPT_AE/FitnessGoals',
                            recommendations_resource='CindyPT_AE/AIRecommendations'))]
        for item in self.patches:
            item.start()
        async def observed(*args, **kwargs):
            return {'availableSessions': self.sessions, 'retainedInstanceCount': sum(x['eventCount'] for x in self.sessions),
                    'exclusionReasons': {}}
        main.fatigue_history_cache.read = AsyncMock(side_effect=observed)

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.api.aclose()
        await self.upstream.aclose()

    def session(self, identity, counts=None, at='2026-11-16T12:00:00+00:00'):
        counts = counts or {'pullup': 15, 'pushup': 30, 'squat': 45}
        return {'sessionId': identity, 'exerciseCounts': counts, 'eventCount': sum(counts.values()),
                'firstEventAt': at, 'lastEventAt': at, 'suitable': all(counts.values()),
                'performance': {'state': 'stable_performance', 'detectorQuality': 'unverified',
                                'exercises': {}, 'estimatedRestGaps': []}}

    def summary(self, identity, rounds=3, duration=1200, reason='timer', source='webcam', counts=None):
        counts = counts or {'pullup': 15, 'pushup': 30, 'squat': 45}
        return {'sessionId': identity, 'rounds': rounds, 'durationSeconds': duration,
                'reps': counts, 'status': 'completed', 'source': source, 'completionReason': reason}

    async def test_full_workout_selected_over_newer_short_test_and_deduplicated(self):
        short_counts = {'pullup': 1, 'pushup': 0, 'squat': 0}
        self.sessions = [self.session('short', short_counts, '2026-11-16T13:00:00+00:00'), self.session('full')]
        self.summaries = [self.summary('full'), self.summary('short', 0, 40, 'explicit_stop', counts=short_counts)]
        result = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(result['latestWorkout']['sessionId'], 'short')
        self.assertEqual(result['coachingWorkout']['sessionId'], 'full')
        self.assertEqual(result['selectionStatus'], 'sufficient')
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()['recommendations']
        self.assertEqual(advice['sourceWorkoutId'], 'full')
        self.assertIsNone(advice['proposedTargetRounds'])
        self.assertEqual(advice['detectorReliability'], 'detector_quality_unknown')
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 201)
        self.assertTrue((await self.api.post('/api/cindypt/recommendations/generate')).json()['alreadySaved'])
        self.assertEqual(len(self.saved), 1)

    async def test_forty_second_session_cannot_drive_coaching(self):
        counts = {'pullup': 1, 'pushup': 0, 'squat': 0}
        self.sessions = [self.session('short', counts)]
        self.summaries = [self.summary('short', 0, 40, 'explicit_stop', counts=counts)]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertFalse(progress['available'])
        self.assertEqual(progress['latestWorkout']['durationSeconds'], 40)
        self.assertEqual(progress['selectionStatus'], 'insufficient_data')
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 409)
        self.assertEqual(self.saved, [])

    async def test_unverified_summary_crash_and_mismatch_are_excluded(self):
        self.sessions = [self.session('crash'), self.session('unverified')]
        self.summaries = [self.summary('unverified', source='simulator')]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertFalse(progress['available'])
        self.assertEqual(progress['latestWorkout']['status'], 'no_verified_summary')
        self.assertIsNone(progress['coachingWorkout'])
        self.summaries = [self.summary('unverified', counts={'pullup': 1, 'pushup': 0, 'squat': 0})]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertFalse(progress['available'])

    async def test_explicit_stop_has_limited_advice_without_target_change(self):
        self.sessions = [self.session('practice')]
        self.summaries = [self.summary('practice', duration=300, reason='explicit_stop')]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['selectionStatus'], 'not_comparable')
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()['recommendations']
        self.assertFalse(advice['planChangeAppropriate'])
        self.assertIsNone(advice['proposedTargetRounds'])
        self.assertEqual((await self.api.post('/api/cindypt/recommendations/generate')).status_code, 409)
        self.assertEqual(self.saved, [])

    async def test_verified_detector_can_support_a_target_proposal(self):
        self.sessions = [self.session('full')]
        self.sessions[0]['performance']['detectorQuality'] = 'verified'
        self.sessions[0]['performance']['exercises'] = {
            'pullup': {'state': 'pace_decreasing', 'paceChangePercent': 32.0}}
        self.sessions[0]['performance']['estimatedRestGaps'] = [{'observedGapSeconds': 14}]
        self.summaries = [self.summary('full')]
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()['recommendations']
        self.assertTrue(advice['planChangeAppropriate'])
        self.assertEqual(advice['proposedTargetRounds'], 4)
        self.assertIn('pullup pace decreased by 32.0%', advice['explanation'])
        self.assertIn('not confirmed rest or fatigue', advice['explanation'])
        self.goal['label'] = 'TEST integration'
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()['recommendations']
        self.assertFalse(advice['planChangeAppropriate'])

    async def test_session_average_pace_is_coaching_context_not_quality_verification(self):
        self.sessions = [self.session('full')]
        self.sessions[0]['performance']['exercises'] = {'pushup': {
            'state': 'stable_performance', 'sessionAveragePace': {
                'secondsPerRep': 1.5, 'repsPerMinute': 40,
                'acceptedIntervalCount': 20, 'possibleIntervalCount': 22,
                'coverage': 0.909, 'unavailableReason': None}}}
        self.summaries = [self.summary('full')]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['coachingWorkout']['performance']['exercises']['pushup']
                         ['sessionAveragePace']['secondsPerRep'], 1.5)
        advice = (await self.api.get('/api/cindypt/recommendations/current')).json()['recommendations']
        self.assertEqual(advice['observedMetrics']['performance']['exercises']['pushup']
                         ['sessionAveragePace']['acceptedIntervalCount'], 20)
        self.assertIn('pushup 1.5 s/rep', advice['explanation'])
        self.assertFalse(advice['planChangeAppropriate'])
        self.assertIsNone(advice['proposedTargetRounds'])

    async def test_missing_summary_and_worker_crash_stay_visible_without_advice(self):
        self.sessions = [self.session('crashed')]
        progress = (await self.api.get('/api/cindypt/progress')).json()
        self.assertEqual(progress['latestWorkout']['sessionId'], 'crashed')
        self.assertEqual(progress['latestWorkout']['status'], 'no_verified_summary')
        self.assertFalse(progress['available'])
        self.assertFalse((await self.api.get('/api/cindypt/recommendations/current')).json()['available'])

    async def test_malformed_summary_fails_closed(self):
        self.summaries = [{'sessionId': 'bad', 'rounds': -1, 'durationSeconds': 1200, 'status': 'completed'}]
        response = await self.api.get('/api/cindypt/progress')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail']['code'], 'invalid_content')
