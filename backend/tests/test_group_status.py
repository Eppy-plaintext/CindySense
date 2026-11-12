import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend import main


class GroupStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fanout_is_reported(self):
        client = SimpleNamespace(
            group_members=AsyncMock(return_value=['TinyIoT/CindySense_AE/RepetitionCount',
                                                  'TinyIoT/CindySense_AE/WorkoutSummary']),
            fanout_retrieve=AsyncMock(return_value={'m2m:agr': {'m2m:rsp': [
                {'rsc': 2000, 'pc': {'m2m:cnt': {'rn': 'RepetitionCount', 'cni': 3}}},
                {'rsc': 4103}]}}))
        settings = SimpleNamespace(group_origin='monitor-origin',
                                   group_resource='CindySense_AE/WorkoutMonitoring_GRP')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url='http://testserver') as api:
            with patch.object(main, 'tinyiot', client), patch.object(main, 'settings', settings):
                response = await api.get('/api/onem2m/group/workout-monitoring')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['overall_status'], 'partial_failure')
        self.assertEqual(response.json()['member_statuses'], [2000, 4103])
