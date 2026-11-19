import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.server.mcpserver import MCPServer

from backend.cindypt_mcp_agent import CindyPTMcpAgent, CindyPTMcpWorkflows
from backend.cindypt_mcp_mec import MecToolConfig, create_mec_mcp_server
from backend.cindypt_mcp_onem2m import OneM2MToolConfig, create_onem2m_mcp_server
from backend.mec_client import MecClientError


class FakeTinyIoT:
    def __init__(self, *args, **kwargs):
        self.published = []

    async def retrieve(self, path, origin):
        if path == 'summary/session-1':
            return {'m2m:cin': {'ri': 'summary-ri', 'con': {
                'workoutId': 'session-1', 'sessionId': 'session-1', 'status': 'completed',
                'rounds': 2, 'durationSeconds': 1200, 'pullups': 10, 'pushups': 20, 'squats': 30,
                'source': 'webcam', 'completionReason': 'timer'}}}
        raise AssertionError(path)

    @staticmethod
    def parse_content_instance(payload):
        return payload['m2m:cin']['con'], payload['m2m:cin']


def fake_servers(published, *, summary_result=None, goal_result=None, mec_result=None,
                 wlan_result=None):
    one = MCPServer('one')
    mec = MCPServer('mec')

    @one.tool()
    async def get_completed_workout_summary(resource_path: str, identity: str) -> dict:
        if summary_result is not None:
            return summary_result
        return {'ok': True, 'identity': identity, 'workoutId': 'session-1', 'summary': {
            'workoutId': 'session-1', 'sessionId': 'session-1', 'status': 'completed',
            'rounds': 2, 'durationSeconds': 1200, 'pullups': 10, 'pushups': 20, 'squats': 30},
            'completionReason': 'timer'}

    @one.tool()
    async def get_current_goal() -> dict:
        if goal_result is not None:
            return goal_result
        return {'ok': True, 'goal': {'targetRounds': 3, 'durationSeconds': 1200}, 'goalVersion': 'goal-ri'}

    @one.tool()
    async def get_workout_evidence(workout: dict, goal: dict) -> dict:
        return {'ok': True, 'sufficient': True, 'matchingWorkout': {
            'sessionId': workout['sessionId'], 'comparable': True, 'summaryVerified': True,
            'suitable': True, 'performance': None}}

    @one.tool()
    async def publish_recommendation(workout_id: str, goal_version: str, recommendation: dict) -> dict:
        published.append((workout_id, goal_version, recommendation))
        return {'ok': True, 'created': True, 'duplicate': False}

    @mec.tool()
    async def discover_wlan_service(refresh: bool = False) -> dict:
        if mec_result is not None:
            return mec_result
        return {'ok': False, 'available': False, 'error': {'code': 'service_discovery_disabled'}}

    @mec.tool()
    async def get_wlan_access_points() -> dict:
        if wlan_result is not None:
            return wlan_result
        return {'ok': False, 'available': False, 'error': {'code': 'mec_disabled'}}
    return one, mec


class CindyPTMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_mec_protocol_discovery_and_disabled_response(self):
        server = create_mec_mcp_server(MecToolConfig(False, False, '', '', 1, 1, False))
        async with Client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, {'discover_wlan_service', 'get_wlan_access_points'})
            result = await client.call_tool('discover_wlan_service', {})
        self.assertEqual(result.structured_content['error']['code'], 'service_discovery_disabled')

    async def test_mec_protocol_invokes_mocked_discovery_and_wlan_read(self):
        calls = []

        class Registry:
            def __init__(self, *args, **kwargs):
                pass

            async def select_wlan(self, *, refresh=False):
                calls.append(('discovery', refresh))
                return type('Selection', (), {'as_dict': lambda self: {
                    'endpoint': 'https://mec.example/wai/v2', 'dynamic_discovery': True}})()

        class Wlan:
            def __init__(self, *args, **kwargs):
                pass

            async def fetch_access_points(self):
                calls.append(('wlan', None))
                return [{'apId': {'macId': '00:11:22:33:44:55'}, 'staCount': 1}]

        config = MecToolConfig(True, True, 'https://mec.example/services',
                               'https://mec.example/wai/v2', 1, 1, False)
        with patch('backend.cindypt_mcp_mec.MecServiceRegistry', Registry), \
             patch('backend.cindypt_mcp_mec.MecWlanClient', Wlan):
            async with Client(create_mec_mcp_server(config)) as client:
                discovery = await client.call_tool('discover_wlan_service', {})
                wlan = await client.call_tool('get_wlan_access_points', {})
        self.assertTrue(discovery.structured_content['available'])
        self.assertTrue(wlan.structured_content['available'])
        self.assertEqual(wlan.structured_content['accessPoints'][0]['staCount'], 1)
        self.assertEqual(calls, [('discovery', False), ('wlan', None)])

    async def test_onem2m_protocol_tool_validates_summary_identity(self):
        config = OneM2MToolConfig('http://fixture', 'CindyPT', 'CWebcam', 'summary', 'goal', 'reps', 'recommendations')
        with patch('backend.cindypt_mcp_onem2m.TinyIoTClient', FakeTinyIoT):
            async with Client(create_onem2m_mcp_server(config)) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                result = await client.call_tool('get_completed_workout_summary',
                                                {'resource_path': 'summary/session-1', 'identity': 'summary-ri'})
        self.assertTrue(result.structured_content['ok'])
        self.assertEqual(names, {'get_completed_workout_summary', 'get_current_goal',
                                 'get_workout_evidence', 'publish_recommendation'})

    async def test_workflow_uses_both_mcp_servers_persists_and_deduplicates(self):
        published = []
        one, mec = fake_servers(published)
        with tempfile.TemporaryDirectory() as temporary:
            store = CindyPTMcpWorkflows(Path(temporary) / 'mcp.sqlite3')
            agent = CindyPTMcpAgent(store, one, mec)
            first = await agent.run('summary/session-1', 'summary-ri')
            second = await agent.run('summary/session-1', 'summary-ri')
            recovered = CindyPTMcpWorkflows(Path(temporary) / 'mcp.sqlite3')
            recovered_state = recovered.get('summary-ri')
        self.assertEqual(first.state, 'completed')
        self.assertTrue(first.recommendation_published)
        self.assertIn('queried_mec_service', first.trace)
        self.assertEqual(second.trace, ('already_completed',))
        self.assertEqual(len(published), 1)
        self.assertTrue(recovered_state['recommendationPublished'])

    async def test_mec_unauthorized_is_structured_not_a_false_available_result(self):
        class UnauthorizedClient:
            def __init__(self, *args, **kwargs):
                pass
            async def fetch_access_points(self):
                raise MecClientError('http_error', 'MEC WLAN returned HTTP 401.', 401)
        config = MecToolConfig(True, False, '', 'https://mec.example', 1, 1, False)
        with patch('backend.cindypt_mcp_mec.MecWlanClient', UnauthorizedClient):
            async with Client(create_mec_mcp_server(config)) as client:
                result = await client.call_tool('get_wlan_access_points', {})
        self.assertFalse(result.structured_content['available'])
        self.assertEqual(result.structured_content['error']['status_code'], 401)

    async def test_missing_summary_defers_without_publication(self):
        published = []
        one, mec = fake_servers(published, summary_result={
            'ok': False, 'error': {'code': 'resource_not_found'}})
        with tempfile.TemporaryDirectory() as temporary:
            store = CindyPTMcpWorkflows(Path(temporary) / 'mcp.sqlite3')
            result = await CindyPTMcpAgent(store, one, mec).run('summary/missing', 'missing-ri')
            self.assertEqual(store.get('missing-ri')['state'], 'pending')
        self.assertEqual(result.state, 'pending')
        self.assertEqual(published, [])

    async def test_malformed_goal_defers_without_publication(self):
        published = []
        one, mec = fake_servers(published, goal_result={'ok': True, 'goalVersion': 'goal-ri'})
        with tempfile.TemporaryDirectory() as temporary:
            result = await CindyPTMcpAgent(CindyPTMcpWorkflows(
                Path(temporary) / 'mcp.sqlite3'), one, mec).run('summary/session-1', 'summary-ri')
        self.assertEqual(result.state, 'pending')
        self.assertEqual(published, [])

    async def test_mec_401_does_not_block_coaching(self):
        published = []
        one, mec = fake_servers(published, mec_result={
            'ok': False, 'available': False, 'error': {'code': 'http_error', 'status_code': 401}})
        with tempfile.TemporaryDirectory() as temporary:
            result = await CindyPTMcpAgent(CindyPTMcpWorkflows(
                Path(temporary) / 'mcp.sqlite3'), one, mec).run('summary/session-1', 'summary-ri')
        self.assertEqual(result.state, 'completed')
        self.assertEqual(result.mec['serviceDiscovery']['error']['status_code'], 401)
        self.assertEqual(len(published), 1)

    async def test_missing_mec_tools_degrades_to_coaching(self):
        published = []
        one, _ = fake_servers(published)
        incomplete_mec = MCPServer('incomplete-mec')
        with tempfile.TemporaryDirectory() as temporary:
            result = await CindyPTMcpAgent(CindyPTMcpWorkflows(
                Path(temporary) / 'mcp.sqlite3'), one, incomplete_mec).run(
                    'summary/session-1', 'summary-ri')
        self.assertEqual(result.state, 'completed')
        self.assertIn('mec_unavailable', result.trace)
        self.assertEqual(len(published), 1)

    async def test_skipped_workflow_remains_unpublished_after_restart(self):
        published = []
        one, mec = fake_servers(published, summary_result={
            'ok': True, 'summary': {'workoutId': 'session-1', 'sessionId': 'session-1',
                                    'status': 'completed', 'rounds': 1,
                                    'durationSeconds': 600, 'pullups': 5,
                                    'pushups': 10, 'squats': 15},
            'completionReason': 'timer'})
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'mcp.sqlite3'
            first = await CindyPTMcpAgent(CindyPTMcpWorkflows(database), one, mec).run(
                'summary/session-1', 'summary-ri')
            second = await CindyPTMcpAgent(CindyPTMcpWorkflows(database), one, mec).run(
                'summary/session-1', 'summary-ri')
        self.assertEqual(first.state, 'completed')
        self.assertFalse(first.recommendation_published)
        self.assertFalse(second.recommendation_published)
        self.assertEqual(published, [])
