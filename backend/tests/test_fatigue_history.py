import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.fatigue_history import FatigueHistoryCache, read_fatigue_history
from backend.tinyiot import TinyIoTError


BASE = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def event_content(session, sequence, exercise, repetitions=None, round_number=1):
    return {
        'eventId': str(uuid4()),
        'eventSequence': sequence,
        'sessionId': session,
        'exercise': exercise,
        'repetitions': repetitions or sequence,
        'round': round_number,
        'timestamp': (BASE + timedelta(seconds=sequence * 2)).isoformat(),
        'source': 'webcam',
    }


class FakeClient:
    def __init__(self, contents, retained_count=None):
        self.contents = contents
        self.retained_count = retained_count if retained_count is not None else len(contents)
        self.list_calls = []
        self.latest_identity = 'latest-1'
        self.retrieve_calls = []

    async def retrieve(self, resource, origin):
        self.retrieve_calls.append(resource)
        if resource == 'CindySense_AE/RepetitionCount':
            return {'m2m:cnt': {'cni': self.retained_count, 'mni': 1000}}
        if resource.endswith('/la'):
            return {'m2m:cin': {'ri': self.latest_identity, 'con': {}}}
        return {'m2m:cin': {'con': self.contents[resource]}}

    async def list_content_instances(self, resource, origin, **kwargs):
        self.list_calls.append((resource, origin, kwargs))
        return list(self.contents)

    @staticmethod
    def parse_content_instance(payload):
        return payload['m2m:cin']['con'], payload['m2m:cin']


class FatigueHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_container_cache_does_not_request_missing_latest_instance(self):
        client = FakeClient({})
        result = await FatigueHistoryCache().read(client, 'CindySense_AE/RepetitionCount', 'reader')
        self.assertEqual(result['availableSessions'], [])
        self.assertFalse(any(path.endswith('/la') for path in client.retrieve_calls))

    async def test_pagination_and_exclusions_are_reported(self):
        contents = {}
        refs = []
        for index, exercise in enumerate(('pullup', 'pushup', 'squat'), 1):
            ref = f'CindySense_AE/RepetitionCount/e{index}'
            refs.append(ref)
            contents[ref] = event_content('webcam-good', index, exercise)
        bad = 'CindySense_AE/RepetitionCount/bad'
        synthetic = 'CindySense_AE/RepetitionCount/synthetic'
        diagnostic = 'CindySense_AE/RepetitionCount/diagnostic'
        refs.extend((bad, synthetic, diagnostic))
        contents[bad] = {'not': 'a repetition event'}
        contents[synthetic] = {**event_content('webcam-good', 4, 'squat'), 'synthetic': True}
        contents[diagnostic] = {**event_content('diag-run', 5, 'squat'), 'source': 'live-audit'}
        client = FakeClient(contents, retained_count=len(refs))
        client.contents = {**contents, **{ref: contents[ref] for ref in refs}}
        result = await read_fatigue_history(client, 'CindySense_AE/RepetitionCount', 'CDashboard')
        self.assertEqual(client.list_calls[0][2]['expected_count'], len(refs))
        self.assertEqual(result['retrievedInstanceCount'], len(refs))
        self.assertEqual(result['validEventCount'], 3)
        self.assertEqual(result['excludedEventCount'], 3)
        self.assertEqual(result['exclusionReasons']['malformed_repetition_event'], 1)
        self.assertEqual(result['exclusionReasons']['synthetic'], 1)
        self.assertEqual(result['exclusionReasons']['non_webcam_or_diagnostic_source'], 1)

    async def test_default_selection_uses_latest_suitable_session_without_merging(self):
        contents = {}
        refs = []
        for session, offset in (('webcam-old', 0), ('webcam-latest', 100)):
            for sequence, exercise in enumerate(
                    ['pullup'] * 5 + ['pushup'] * 5 + ['squat'] * 5, 1):
                ref = f'CindySense_AE/RepetitionCount/{session}-{sequence}'
                refs.append(ref)
                content = event_content(session, sequence, exercise, round_number=1)
                content['timestamp'] = (BASE + timedelta(seconds=offset + sequence * 2)).isoformat()
                contents[ref] = content
        client = FakeClient(contents, retained_count=len(refs))
        result = await read_fatigue_history(client, 'CindySense_AE/RepetitionCount', 'CDashboard')
        self.assertEqual(result['selectedSessionId'], 'webcam-latest')
        self.assertEqual(result['selectedEventCount'], 15)
        self.assertEqual(result['validEventCount'], 30)
        self.assertFalse(result['dataSufficient'])
        self.assertTrue(all(item['reasonUnknown'] == 'detector_quality_unverified'
                            for item in result['results']))

    async def test_explicit_session_selection_and_insufficient_data(self):
        contents = {}
        refs = []
        for sequence in range(1, 5):
            ref = f'CindySense_AE/RepetitionCount/partial-{sequence}'
            refs.append(ref)
            contents[ref] = event_content('webcam-partial', sequence, 'squat')
        client = FakeClient(contents)
        result = await read_fatigue_history(
            client, 'CindySense_AE/RepetitionCount', 'CDashboard', selected_session_id='webcam-partial')
        self.assertEqual(result['selectedSessionId'], 'webcam-partial')
        self.assertFalse(result['dataSufficient'])
        self.assertTrue(all(item['fatigueLevel'] == 'unknown' for item in result['results']))

    async def test_cache_hit_avoids_second_full_scan(self):
        contents = {}
        for sequence, exercise in enumerate(['pullup', 'pushup', 'squat'] * 5, 1):
            contents[f'CindySense_AE/RepetitionCount/{sequence}'] = event_content('cached', sequence, exercise)
        client = FakeClient(contents)
        cache = FatigueHistoryCache()
        first = await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'cached')
        second = await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'cached')
        self.assertEqual(first, second)
        self.assertEqual(len(client.list_calls), 1)
        self.assertEqual(cache.stats(), {'cacheHits': 1, 'fullScans': 1, 'entries': 1})

    async def test_latest_identity_invalidates_cache(self):
        contents = {}
        for sequence, exercise in enumerate(['pullup', 'pushup', 'squat'] * 5, 1):
            contents[f'CindySense_AE/RepetitionCount/{sequence}'] = event_content('changing', sequence, exercise)
        client = FakeClient(contents)
        cache = FatigueHistoryCache()
        await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'changing')
        client.latest_identity = 'latest-2'
        await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'changing')
        self.assertEqual(len(client.list_calls), 2)
        self.assertEqual(cache.stats()['fullScans'], 2)

    async def test_concurrent_requests_share_one_full_scan(self):
        contents = {}
        for sequence, exercise in enumerate(['pullup', 'pushup', 'squat'] * 5, 1):
            contents[f'CindySense_AE/RepetitionCount/{sequence}'] = event_content('concurrent', sequence, exercise)
        client = FakeClient(contents)
        cache = FatigueHistoryCache()
        results = await asyncio.gather(*[
            cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'concurrent')
            for _ in range(3)
        ])
        self.assertEqual([result['selectedSessionId'] for result in results], ['concurrent'] * 3)
        self.assertEqual(len(client.list_calls), 1)
        self.assertEqual(cache.stats()['fullScans'], 1)

    async def test_marker_timeout_does_not_poison_cache_and_recovers(self):
        contents = {}
        for sequence, exercise in enumerate(['pullup', 'pushup', 'squat'] * 5, 1):
            contents[f'CindySense_AE/RepetitionCount/{sequence}'] = event_content('recover', sequence, exercise)

        class FlakyClient(FakeClient):
            def __init__(self, contents):
                super().__init__(contents)
                self.fail_latest = True

            async def retrieve(self, resource, origin):
                if resource.endswith('/la') and self.fail_latest:
                    self.fail_latest = False
                    raise TinyIoTError('tinyiot_timeout', 'simulated timeout', 504)
                return await super().retrieve(resource, origin)

        client = FlakyClient(contents)
        cache = FatigueHistoryCache()
        with self.assertRaises(TinyIoTError):
            await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'recover')
        result = await cache.read(client, 'CindySense_AE/RepetitionCount', 'CDashboard', 'recover')
        self.assertEqual(result['selectedSessionId'], 'recover')
        self.assertEqual(cache.stats()['fullScans'], 1)


if __name__ == '__main__':
    unittest.main()
