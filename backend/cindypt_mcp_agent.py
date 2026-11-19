"""Explicitly invoked, optional CindyPT MCP workflow coordinator.

This is separate from the existing notification-driven coaching queue.  It has
its own SQLite journal and does nothing until a caller invokes ``run``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
import time
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp import Client, StdioServerParameters

from .cindypt import WorkoutSummary, analyze, recommend

log = logging.getLogger(__name__)

READ_RETRIES = 2
RETRYABLE_CODES = {'tinyiot_unreachable', 'tinyiot_error', 'recommendation_unconfirmed',
                   'timeout', 'connection_error', 'service_discovery_timeout',
                   'service_discovery_connection_error', 'service_discovery_http_error'}


class ToolConnection(Protocol):
    async def __aenter__(self) -> 'ToolConnection': ...
    async def __aexit__(self, exc_type, exc, traceback) -> None: ...
    async def list_tool_names(self) -> set[str]: ...
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class OfficialMcpConnection:
    """Small adapter over the official SDK that works with stdio or in-process servers."""
    def __init__(self, target: Any, *, timeout_seconds: float = 15.0):
        self._target = target
        self._timeout_seconds = timeout_seconds
        self._client: Client | None = None

    async def __aenter__(self) -> 'OfficialMcpConnection':
        self._client = Client(self._target, read_timeout_seconds=self._timeout_seconds)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        assert self._client is not None
        await self._client.__aexit__(exc_type, exc, traceback)

    async def list_tool_names(self) -> set[str]:
        assert self._client is not None
        return {tool.name for tool in (await self._client.list_tools()).tools}

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        result = await self._client.call_tool(name, arguments)
        content = result.structured_content
        if not isinstance(content, dict):
            # Some valid MCP servers expose JSON only as TextContent.  Accept
            # that protocol representation, but reject any non-object result.
            blocks = getattr(result, 'content', ())
            for block in blocks:
                text = getattr(block, 'text', None)
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict):
                        content = parsed
                        break
        if not isinstance(content, dict):
            raise RuntimeError(f'MCP tool {name} returned no structured object')
        return dict(content)


@dataclass(frozen=True)
class WorkflowResult:
    identity: str
    state: str
    recommendation_published: bool
    mec: dict[str, Any]
    trace: tuple[str, ...]


class CindyPTMcpWorkflows:
    """Durable idempotency journal for the separately-enabled MCP coaching path."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mcp_workflows ('
                       'identity TEXT PRIMARY KEY, resource TEXT NOT NULL, state TEXT NOT NULL, '
                       'attempts INTEGER NOT NULL DEFAULT 0, recommendation_published INTEGER NOT NULL DEFAULT 0, '
                       'detail TEXT NOT NULL DEFAULT \'{}\', updated_at REAL NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, resource: str, identity: str) -> bool:
        with self._db() as db:
            result = db.execute('INSERT OR IGNORE INTO mcp_workflows '
                                '(identity, resource, state, updated_at) VALUES (?, ?, \'pending\', ?)',
                                (identity, resource, time.time()))
            return result.rowcount == 1

    def get(self, identity: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute('SELECT resource, state, attempts, recommendation_published, detail '
                             'FROM mcp_workflows WHERE identity=?', (identity,)).fetchone()
        if row is None:
            return None
        resource, state, attempts, published, detail = row
        return {'resource': resource, 'state': state, 'attempts': attempts,
                'recommendationPublished': bool(published), 'detail': json.loads(detail)}

    def update(self, identity: str, state: str, *, published: bool = False,
               detail: dict[str, Any] | None = None, increment_attempts: bool = False) -> None:
        with self._db() as db:
            db.execute('UPDATE mcp_workflows SET state=?, recommendation_published=?, detail=?, '
                       'attempts=attempts+?, updated_at=? WHERE identity=?',
                       (state, int(published), json.dumps(detail or {}, separators=(',', ':')),
                        int(increment_attempts), time.time(), identity))


class CindyPTMcpAgent:
    REQUIRED_ONEM2M_TOOLS = frozenset({'get_completed_workout_summary', 'get_current_goal',
                                      'get_workout_evidence', 'publish_recommendation'})
    REQUIRED_MEC_TOOLS = frozenset({'discover_wlan_service', 'get_wlan_access_points'})

    def __init__(self, workflows: CindyPTMcpWorkflows, onem2m_target: Any, mec_target: Any,
                 *, timeout_seconds: float = 15.0):
        self.workflows = workflows
        self.onem2m_target = onem2m_target
        self.mec_target = mec_target
        self.timeout_seconds = timeout_seconds

    async def _call(self, connection: ToolConnection, name: str, arguments: dict[str, Any],
                    *, retries: int = READ_RETRIES) -> dict[str, Any]:
        for attempt in range(retries + 1):
            try:
                result = await asyncio.wait_for(connection.call(name, arguments), self.timeout_seconds)
            except (TimeoutError, OSError) as exc:
                if attempt < retries:
                    continue
                raise RuntimeError(f'MCP tool {name} timed out or disconnected') from exc
            error = result.get('error') if isinstance(result, dict) else None
            code = error.get('code') if isinstance(error, dict) else None
            if result.get('ok') is not False or code not in RETRYABLE_CODES or attempt >= retries:
                return result
        raise AssertionError('unreachable')

    @staticmethod
    def _require(result: dict[str, Any], label: str) -> dict[str, Any]:
        if result.get('ok') is not True:
            error = result.get('error') if isinstance(result.get('error'), dict) else {}
            raise ValueError(f'{label}: {error.get("code", "unknown_error")}')
        return result

    async def run(self, resource: str, identity: str) -> WorkflowResult:
        """Run a single explicit workflow; repeated calls are safe and resumable."""
        if self.workflows.get(identity) is None:
            self.workflows.enqueue(resource, identity)
        existing = self.workflows.get(identity)
        assert existing is not None
        if existing['state'] == 'completed':
            return WorkflowResult(identity, 'completed', existing['recommendationPublished'],
                                  existing['detail'].get('mec', {}),
                                  ('already_completed',))
        trace: list[str] = []
        self.workflows.update(identity, 'running', detail={'trace': trace}, increment_attempts=True)
        try:
            async with AsyncExitStack() as stack:
                one = await stack.enter_async_context(OfficialMcpConnection(
                    self.onem2m_target, timeout_seconds=self.timeout_seconds))
                if not self.REQUIRED_ONEM2M_TOOLS <= await one.list_tool_names():
                    raise RuntimeError('oneM2M MCP server does not expose the required typed tools')
                trace.append('discovered_onem2m_tools')

                summary_result = self._require(await self._call(one, 'get_completed_workout_summary',
                    {'resource_path': resource, 'identity': identity}), 'summary')
                summary = summary_result.get('summary')
                if not isinstance(summary, dict):
                    raise ValueError('summary: malformed tool response')
                validated_summary = WorkoutSummary.model_validate(summary)
                trace.append('read_completed_summary')
                if summary_result.get('completionReason') != 'timer' or validated_summary.durationSeconds < 1140:
                    self.workflows.update(identity, 'completed', detail={'mec': {}, 'trace': trace, 'skipped': 'not_20_minutes'})
                    return WorkflowResult(identity, 'completed', False, {}, tuple(trace))

                goal_result = self._require(await self._call(one, 'get_current_goal', {}), 'goal')
                if not isinstance(goal_result.get('goal'), dict) or not isinstance(goal_result.get('goalVersion'), str):
                    raise ValueError('goal: malformed tool response')
                trace.append('read_goal')
                evidence = self._require(await self._call(one, 'get_workout_evidence',
                    {'workout': summary, 'goal': goal_result['goal']}), 'retained evidence')
                trace.append('validated_retained_evidence')
                if not evidence.get('sufficient'):
                    self.workflows.update(identity, 'completed', detail={'mec': {}, 'trace': trace, 'skipped': 'insufficient_evidence'})
                    return WorkflowResult(identity, 'completed', False, {}, tuple(trace))

                # MEC is advisory only.  Its structured unavailable response never blocks coaching.
                mec_detail = {'available': False, 'reason': 'mec_server_unavailable'}
                try:
                    mec = await stack.enter_async_context(OfficialMcpConnection(
                        self.mec_target, timeout_seconds=self.timeout_seconds))
                    if not self.REQUIRED_MEC_TOOLS <= await mec.list_tool_names():
                        raise RuntimeError('MEC MCP server does not expose the required typed tools')
                    trace.append('discovered_mec_tools')
                    mec_result = await self._call(mec, 'discover_wlan_service', {})
                    mec_detail = {'serviceDiscovery': mec_result}
                    trace.append('queried_mec_service')
                    if mec_result.get('ok') is True:
                        mec_detail['accessPoints'] = await self._call(mec, 'get_wlan_access_points', {})
                        trace.append('queried_mec_wlan')
                except (RuntimeError, OSError, TimeoutError, ValueError, ExceptionGroup) as exc:
                    log.warning('Optional MEC MCP information unavailable: %s', exc)
                    trace.append('mec_unavailable')

                workout = {'workoutId': summary_result['workoutId'], 'roundsCompleted': summary['rounds'],
                           'durationSeconds': summary['durationSeconds'],
                           'reps': validated_summary.exercise_counts(),
                           'source': 'webcam', 'completionReason': 'timer'}
                progress = analyze(goal_result['goal'], workout)
                progress.update({'selectionStatus': 'sufficient', 'detectorReliability': 'detector_quality_unknown',
                                 'coachingWorkout': evidence['matchingWorkout'],
                                 'trainingPlanChangeAppropriate': False})
                recommendation = recommend(progress)
                recommendation['goalVersion'] = goal_result['goalVersion']
                publish = self._require(await self._call(one, 'publish_recommendation', {
                    'workout_id': workout['workoutId'], 'goal_version': goal_result['goalVersion'],
                    'recommendation': recommendation}), 'recommendation publication')
                trace.append('published_recommendation' if publish.get('created') else 'duplicate_recommendation')
                self.workflows.update(identity, 'completed', published=True,
                                      detail={'mec': mec_detail, 'trace': trace})
                return WorkflowResult(identity, 'completed', True, mec_detail, tuple(trace))
        except (ValueError, RuntimeError, sqlite3.Error, KeyError, ExceptionGroup) as exc:
            log.warning('CindyPT MCP workflow deferred identity=%s reason=%s', identity, exc)
            self.workflows.update(identity, 'pending', detail={'trace': trace, 'error': str(exc)})
            return WorkflowResult(identity, 'pending', False, {}, tuple(trace))


async def _main_async(resource: str, identity: str, database: str) -> None:
    """Run only when an operator explicitly starts the optional local workflow."""
    root = str(Path(__file__).resolve().parents[1])
    server_env = {'PYTHONPATH': root}
    agent = CindyPTMcpAgent(
        CindyPTMcpWorkflows(database),
        StdioServerParameters(command=sys.executable,
                              args=['-m', 'backend.cindypt_mcp_onem2m'], cwd=root, env=server_env),
        StdioServerParameters(command=sys.executable,
                              args=['-m', 'backend.cindypt_mcp_mec'], cwd=root, env=server_env),
    )
    result = await agent.run(resource, identity)
    print(json.dumps({'identity': result.identity, 'state': result.state,
                      'recommendationPublished': result.recommendation_published,
                      'mec': result.mec, 'trace': list(result.trace)}, indent=2))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Explicitly run one optional CindyPT MCP workflow.')
    parser.add_argument('--summary-resource', required=True)
    parser.add_argument('--summary-identity', required=True)
    parser.add_argument('--database', default=str(Path.home() / '.cindysense' / 'cindypt_mcp.sqlite3'))
    args = parser.parse_args()
    asyncio.run(_main_async(args.summary_resource, args.summary_identity, args.database))


if __name__ == '__main__':
    main()
