import json
import time
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.tinyiot import TinyIoTClient, TinyIoTError
from backend.workout_snapshot import WorkoutSnapshot
from backend import main


def event(exercise, count, source='webcam', session='webcam-20261106T120000Z-12345678', stamp='2026-11-06T12:00:10Z', event_id=None):
    return {'eventId': event_id or str(uuid4()), 'eventSequence': count,
            'sessionId': session, 'exercise': exercise, 'repetitions': count,
            'round': 1, 'timestamp': stamp, 'source': source}


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.records = {}
        self.references = []
        self.requests = []
        self.absolute_refs = False
        self.ignore_limit = False
        self.mni = 100

        def handle(request):
            self.requests.append(request)
            if request.url.path.endswith('/PoseData'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={
                    'm2m:cnt': {'rn': 'PoseData'}})
            if request.url.path.endswith('/PoseData/la'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={
                    'm2m:cin': {'ct': '20261106T120010,000', 'con': json.dumps({
                        'source': 'webcam_live', 'sessionId': 'webcam-20261106T120000Z-12345678',
                        'exercise': 'pushup', 'repCount': 10, 'eventQueueDepth': 5,
                        'eventConfirmed': 5, 'eventRetries': 1, 'eventOverflow': 0,
                        'timestampUtc': '2026-11-06T12:00:10Z'})}})
            if request.url.params.get('rcn') == '6':
                offset = int(request.url.params['ofst'])
                limit = int(request.url.params['lim'])
                page_refs = (self.references[offset:] if self.ignore_limit else
                             self.references[offset:offset + limit])
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={
                    'm2m:rrl': {'rrf': [{'typ': 4, 'val': f'{"http://localhost:3000/" if self.absolute_refs else ""}TinyIoT/CindySense_AE/RepetitionCount/{ref}'}
                                       for ref in page_refs]}})
            if request.url.path.endswith('/RepetitionCount'):
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={
                    'm2m:cnt': {'mni': self.mni, 'cni': len(self.references), 'cbs': 1000}})
            name = request.url.path.rsplit('/', 1)[-1]
            if name in self.records:
                return httpx.Response(200, headers={'X-M2M-RSC': '2000'}, json={
                    'm2m:cin': {'con': json.dumps(self.records[name])}})
            return httpx.Response(404)

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.client = TinyIoTClient('http://localhost:3000/TinyIoT', self.upstream)
        self.snapshot = WorkoutSnapshot()

    async def asyncTearDown(self):
        await self.upstream.aclose()

    def add(self, name, content):
        self.records[name] = content
        self.references.insert(0, name)

    async def read(self):
        return await self.snapshot.read(self.client, 'CindySense_AE/RepetitionCount', 'authorized-read-origin')

    async def test_refresh_and_missed_polls_recover_counts_from_retained_instances(self):
        for index in range(1, 6):
            self.add(f'pull-{index}', event('pullup', index))
        for index in range(1, 11):
            self.add(f'push-{index}', event('pushup', index))
        for index in range(1, 16):
            self.add(f'squat-{index}', event('squat', index))
        result = await self.read()
        webcam = result['sources']['webcam']
        self.assertEqual(webcam['counts'], {'pullup': 5, 'pushup': 10, 'squat': 15})
        self.assertEqual(webcam['completedRounds'], 1)
        self.assertEqual(webcam['startAt'], '2026-11-06T12:00:00+00:00')
        self.assertEqual(result['cachedEventCount'], 30)
        self.assertTrue(all('/TinyIoT/' in str(request.url) for request in self.requests))
        # A new backend snapshot object can reconstruct the same session after page refresh.
        self.snapshot = WorkoutSnapshot()
        self.assertEqual((await self.read())['sources']['webcam']['completedRounds'], 1)

    async def test_duplicate_event_id_sources_and_new_sessions(self):
        first = event('squat', 1)
        self.add('squat-1', first)
        self.add('retry-copy', first)
        self.add('esp-1', event('squat', 1, 'mpu6050', 'esp32-11111111'))
        result = await self.read()
        self.assertEqual(result['cachedEventCount'], 2)
        self.assertEqual(result['sources']['webcam']['counts']['squat'], 1)
        self.assertEqual(result['sources']['mpu6050']['counts']['squat'], 1)
        self.add('new-session', event('pullup', 1, session='webcam-20261106T121000Z-87654321', stamp='2026-11-06T12:10:10Z'))
        result = await self.read()
        self.assertEqual(result['sources']['webcam']['counts'], {'pullup': 1, 'pushup': 0, 'squat': 0})
        self.assertTrue(result['sources']['mpu6050']['startInferred'])

    async def test_live_audit_synthetic_content_is_excluded_from_real_counts(self):
        zero_count = event('squat', 0)
        zero_count['eventSequence'] = 1
        self.add('real-zero', zero_count)
        self.add('real-one', event('squat', 1))
        self.add('live-audit-ec78fc72da624a4fbcd7bcc264b819b3', {
            'eventId': 'ec78fc72-da62-4a4f-bcd7-bcc264b819b3',
            'eventSequence': 999999,
            'sessionId': 'live-audit-20261111T031124Z',
            'exercise': 'squat', 'repetitions': 0, 'round': 0,
            'timestamp': '2026-11-11T03:11:24.130393+00:00',
            'source': 'live-audit', 'synthetic': True,
        })
        result = await self.read()
        self.assertEqual(result['retainedInstanceCount'], 3)
        self.assertEqual(result['cachedEventCount'], 2)
        self.assertEqual(result['sources']['webcam']['counts']['squat'], 1)
        self.assertEqual(result['sources']['webcam']['eventCount'], 2)
        self.assertIsNone(result['sources']['mpu6050'])

    async def test_malformed_non_audit_content_still_fails_closed(self):
        for changes in ({'round': 0}, {'source': 'live-audit', 'synthetic': False},
                        {'source': 'webcam', 'synthetic': True}):
            with self.subTest(changes=changes):
                self.snapshot = WorkoutSnapshot()
                self.records.clear()
                self.references.clear()
                malformed = event('squat', 1)
                malformed.update(changes)
                self.add('malformed', malformed)
                with self.assertRaises(TinyIoTError) as caught:
                    await self.read()
                self.assertEqual(caught.exception.code, 'invalid_content')

    async def test_cached_events_survive_tinyiot_retention_changes(self):
        self.add('first', event('squat', 1))
        await self.read()
        self.references.clear()
        result = await self.read()
        self.assertEqual(result['sources']['webcam']['counts']['squat'], 1)
        self.assertEqual(result['cacheOnlyEvents'], 1)

    async def test_rapid_reordered_duplicate_burst_uses_absolute_counts(self):
        for index in range(30, 0, -1):
            self.add(f'push-{index}', event('pushup', index,
                     stamp=f'2026-11-06T12:00:{index:02d}Z'))
        self.add('retry-copy', self.records['push-30'])
        result = await self.read()
        self.assertEqual(result['sources']['webcam']['counts']['pushup'], 30)
        self.assertEqual(result['cachedEventCount'], 30)
        self.assertEqual(result['retainedInstanceCount'], 31)
        self.assertIn('retrievedAt', result)
        self.assertGreaterEqual(result['retrievalMs'], 0)

    async def test_delayed_old_session_event_does_not_replace_new_session(self):
        self.add('new', event('pushup', 2, session='webcam-20261106T121000Z-new',
                              stamp='2026-11-06T12:10:20Z'))
        self.add('old-late', event('pushup', 50, session='webcam-20261106T120000Z-old',
                                   stamp='2026-11-06T12:11:00Z'))
        result = await self.read()
        self.assertEqual(result['sources']['webcam']['sessionId'], 'webcam-20261106T121000Z-new')
        self.assertEqual(result['sources']['webcam']['counts']['pushup'], 2)

    async def test_retention_limit_is_reported(self):
        for index in range(100):
            self.add(f'event-{index}', event('pushup', index + 1))
        result = await self.read()
        self.assertTrue(result['retentionAtCapacity'])
        self.assertEqual(result['retention']['mni'], 100)

    async def test_missed_polls_retrieve_thirty_new_events(self):
        self.add('initial', event('pushup', 1))
        await self.read()
        for index in range(2, 32):
            self.add(f'next-{index}', event('pushup', index))
        result = await self.read()
        self.assertEqual(result['sources']['webcam']['counts']['pushup'], 31)
        self.assertEqual(result['cachedEventCount'], 31)

    async def test_twenty_minute_event_timeline_with_refresh(self):
        start = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        for index in range(300):
            stamp = (start + timedelta(seconds=index * 4)).isoformat()
            self.add(f'long-{index}', event('pushup', index + 1, stamp=stamp))
        self.snapshot._container = {'mni': 1000, 'cni': 300}
        self.snapshot._container_checked = time.perf_counter()
        result = await self.read()
        self.assertEqual(result['cachedEventCount'], 300)
        self.assertEqual(result['sources']['webcam']['counts']['pushup'], 300)
        self.snapshot = WorkoutSnapshot()
        self.snapshot._container = {'mni': 1000, 'cni': 300}
        self.snapshot._container_checked = time.perf_counter()
        self.assertEqual((await self.read())['sources']['webcam']['counts']['pushup'], 300)

    async def test_invalid_reference_fails_closed(self):
        self.references.append('../other')
        with self.assertRaises(TinyIoTError):
            await self.read()

    async def test_listing_pages_and_same_host_absolute_references(self):
        self.absolute_refs = True
        self.add('one', event('squat', 1))
        self.add('two', event('squat', 2))
        self.add('three', event('squat', 3))
        refs = await self.client.list_content_instances('CindySense_AE/RepetitionCount', 'authorized-read-origin', page_size=2)
        self.assertEqual(refs, ['CindySense_AE/RepetitionCount/three',
                                'CindySense_AE/RepetitionCount/two',
                                'CindySense_AE/RepetitionCount/one'])

    async def test_live_tinyiot_ignoring_lim_needs_one_listing_request(self):
        self.ignore_limit = True
        self.mni = 1000
        for index in range(121):
            self.add(f'push-{index}', event('pushup', index + 1))
        result = await self.read()
        listings = [request for request in self.requests if request.url.params.get('rcn') == '6']
        self.assertEqual(len(listings), 1)
        self.assertEqual(result['retainedInstanceCount'], 121)
        self.assertEqual(result['sources']['webcam']['counts']['pushup'], 121)

    async def test_api_snapshot_uses_backend_aggregation(self):
        self.add('squat-1', event('squat', 1))
        with (patch.object(main, 'tinyiot', self.client),
              patch.object(main, 'workout_snapshot', self.snapshot),
              patch.object(main, 'settings', main.settings.__class__(
                  base_url='http://localhost:3000/TinyIoT',
                  workout_origin='authorized-read-origin',
                  repetition_resource='CindySense_AE/RepetitionCount',
              ))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver') as api:
                response = await api.get('/api/workout/snapshot')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sources']['webcam']['counts']['squat'], 1)
        self.assertEqual(response.json()['sources']['webcam']['completedRounds'], 0)

    async def test_api_reports_webcam_pending_status_without_changing_event_schema(self):
        with (patch.object(main, 'tinyiot', self.client),
              patch.object(main, 'settings', main.settings.__class__(
                  base_url='http://localhost:3000/TinyIoT', webcam_origin='CWebcam'))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver') as api:
                response = await api.get('/api/workout/webcam-status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['eventQueueDepth'], 5)
        self.assertEqual(response.json()['detectedRepetitions'], 10)
        self.assertEqual(self.requests[-1].headers['X-M2M-Origin'], 'CWebcam')
