"""Offline judge demo using two real in-process MCP servers and mocked data.

Run from the repository root: python -m tools.mcp_workflow_demo
No CSE or MEC request is made and no production resource is written.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from backend.cindypt_mcp_agent import CindyPTMcpAgent, CindyPTMcpWorkflows
from backend.tests.test_cindypt_mcp import fake_servers


async def demonstrate():
    published = []
    one, mec = fake_servers(
        published,
        mec_result={'ok': True, 'available': True, 'service': {'name': 'fixture-wlan'}},
        wlan_result={'ok': True, 'available': True, 'accessPoints': [
            {'apId': 'fixture-ap', 'staCount': 1}]},
    )
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / 'mcp.sqlite3'
        workflow = CindyPTMcpWorkflows(database)
        agent = CindyPTMcpAgent(workflow, one, mec)
        first = await agent.run('summary/session-1', 'summary-ri')
        persisted = CindyPTMcpWorkflows(database).get('summary-ri')
        restarted = CindyPTMcpAgent(CindyPTMcpWorkflows(database), one, mec)
        second = await restarted.run('summary/session-1', 'summary-ri')

    operations = {
        'discovered_onem2m_tools': 'oneM2M MCP: list_tools',
        'read_completed_summary': 'oneM2M MCP: get_completed_workout_summary',
        'read_goal': 'oneM2M MCP: get_current_goal',
        'validated_retained_evidence': 'oneM2M MCP: get_workout_evidence',
        'discovered_mec_tools': 'MEC MCP: list_tools',
        'queried_mec_service': 'MEC MCP: discover_wlan_service',
        'queried_mec_wlan': 'MEC MCP: get_wlan_access_points',
        'published_recommendation': 'oneM2M MCP: publish_recommendation',
        'duplicate_recommendation': 'oneM2M MCP: publish_recommendation (deduplicated)',
    }
    print(json.dumps({
        'externalResults': 'MOCKED; MCP transport, tool discovery, orchestration and SQLite are real',
        'operations': [operations.get(step, step) for step in first.trace],
        'validation': 'completed 20-minute summary, goal, and retained evidence accepted',
        'mec': first.mec,
        'recommendation': published[0][2] if published else None,
        'firstState': first.state,
        'persistedState': persisted['state'],
        'restartTrace': list(second.trace),
        'publicationCount': len(published),
        'duplicatePrevented': len(published) == 1 and second.trace == ('already_completed',),
    }, indent=2))
    if first.state != 'completed' or len(published) != 1 or second.trace != ('already_completed',):
        raise SystemExit('MCP demonstration failed')


if __name__ == '__main__':
    asyncio.run(demonstrate())
