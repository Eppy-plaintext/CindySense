import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import httpx

from backend import main
from backend.events import RepetitionEvent
from backend.tests.test_fatigue_history import FakeClient


class SnapshotStub:
    def __init__(self, events):
        self._events = events

    async def read(self, *args, **kwargs):
        return {'retainedInstanceCount': len(self._events)}

    def validated_events(self):
        return list(self._events)


class FatigueAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_fatigue_endpoint_is_read_only_and_reports_unknown_exercises(self):
        start = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        events = [RepetitionEvent(
            eventId=str(uuid4()), eventSequence=index, sessionId='webcam-api',
            exercise='squat', repetitions=index, round=1,
            timestamp=(start + timedelta(seconds=index * 2)).isoformat(), source='webcam')
            for index in range(1, 6)]
        contents = {
            f'CindySense_AE/RepetitionCount/event-{index}': event.content()
            for index, event in enumerate(events, 1)
        }
        client_stub = FakeClient(contents, retained_count=len(events))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver') as client:
            with patch.object(main, 'tinyiot', client_stub):
                response = await client.get('/api/workout/fatigue')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['available'])
        self.assertFalse(body['dataSufficient'])
        self.assertEqual(body['cachedEventCount'], 5)
        self.assertEqual(body['results'][2]['exercise'], 'squat')
        self.assertEqual(body['results'][2]['fatigueLevel'], 'normal')
        self.assertEqual(body['results'][0]['fatigueLevel'], 'unknown')
        self.assertIn('not medically validated', body['medicalDisclaimer'])


if __name__ == '__main__':
    unittest.main()
