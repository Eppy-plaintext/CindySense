"""Optional, typed MCP tools over CindySense's existing oneM2M read/write APIs.

This module is intentionally not imported by the FastAPI application.  A host
must explicitly run this server or construct it for an MCP client.  It only
uses the same validation and idempotent recommendation writer as CindyPT.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from .cindypt import Goal, WorkoutSummary
from .config import settings
from .fatigue_history import read_fatigue_history
from .tinyiot import TinyIoTClient, TinyIoTError
from .workout_history import build_history


@dataclass(frozen=True)
class OneM2MToolConfig:
    base_url: str
    cindypt_origin: str
    workout_origin: str
    workout_summary_resource: str
    target_plan_resource: str
    repetition_resource: str
    recommendations_resource: str

    @classmethod
    def from_settings(cls) -> 'OneM2MToolConfig':
        return cls(settings.base_url, settings.cindypt_origin, settings.workout_origin,
                   settings.workout_summary_resource, settings.target_plan_resource,
                   settings.repetition_resource, settings.recommendations_resource)


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TinyIoTError):
        return {'ok': False, 'error': {'code': exc.code, 'message': str(exc),
                                       'status_code': exc.status_code,
                                       'retryable': exc.code in {'tinyiot_unreachable', 'tinyiot_error'}}}
    if isinstance(exc, (ValidationError, ValueError)):
        return {'ok': False, 'error': {'code': 'invalid_content', 'message': str(exc),
                                       'status_code': 502, 'retryable': False}}
    return {'ok': False, 'error': {'code': 'internal_error', 'message': 'MCP tool failed.',
                                   'status_code': 500, 'retryable': False}}


def _summary_row(content: dict[str, Any], instance: dict[str, Any], identity: str) -> dict[str, Any]:
    summary = WorkoutSummary.model_validate(content)
    if not summary.status or summary.status.lower() not in ('complete', 'completed'):
        raise ValueError('summary is not completed')
    if content.get('synthetic') is True or content.get('source') != 'webcam':
        raise ValueError('summary is not a real webcam workout')
    if not summary.sessionId or summary.workoutId not in (None, summary.sessionId):
        raise ValueError('webcam workout identity does not match its session')
    instance_identity = str(instance.get('ri') or identity)
    if instance_identity != identity:
        raise ValueError('summary identity changed')
    workout_id = summary.workoutId or summary.sessionId or identity
    return {'identity': identity, 'workoutId': workout_id, 'summary': summary.model_dump(),
            'source': content['source'], 'completionReason': content.get('completionReason'),
            'recordedAt': instance.get('ct')}


def create_onem2m_mcp_server(config: OneM2MToolConfig) -> MCPServer:
    """Create a server without doing any network I/O at import or construction."""
    server = MCPServer('cindysense-onem2m', version='1.0.0',
                       instructions='Typed read-only workout tools plus an idempotent recommendation publisher.')

    def client() -> TinyIoTClient:
        return TinyIoTClient(config.base_url)

    @server.tool()
    async def get_completed_workout_summary(resource_path: str, identity: str) -> dict[str, Any]:
        """Read one completed webcam summary after validating its resource scope and identity."""
        root = config.workout_summary_resource.strip('/')
        path = resource_path.strip('/')
        if not path.startswith(root + '/'):
            return {'ok': False, 'error': {'code': 'invalid_resource_path',
                    'message': 'Summary path is outside the configured container.', 'status_code': 403,
                    'retryable': False}}
        try:
            raw, instance = client().parse_content_instance(
                await client().retrieve(path, config.cindypt_origin))
            if not isinstance(raw, dict):
                raise ValueError('summary content is not an object')
            return {'ok': True, **_summary_row(raw, instance, identity)}
        except Exception as exc:  # Translate only at the MCP boundary.
            return _error(exc)

    @server.tool()
    async def get_current_goal() -> dict[str, Any]:
        """Read and validate the current persisted CindyPT goal and its stable version."""
        try:
            raw, instance = await client().latest_content(config.target_plan_resource, config.cindypt_origin)
            goal = Goal.model_validate(raw).model_dump()
            version = str(instance.get('ri') or instance.get('ct') or '')
            if not version:
                raise ValueError('goal has no stable version')
            return {'ok': True, 'goal': goal, 'goalVersion': version}
        except Exception as exc:
            return _error(exc)

    @server.tool()
    async def get_workout_evidence(workout: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
        """Join retained repetition events to a validated summary using CindyPT's existing policy."""
        try:
            summary = WorkoutSummary.model_validate(workout)
            workout_id = summary.workoutId or summary.sessionId
            if not workout_id:
                raise ValueError('workout identity is missing')
            observed = await read_fatigue_history(client(), config.repetition_resource, config.workout_origin)
            history = build_history(observed, [{'workoutId': workout_id, 'roundsCompleted': summary.rounds,
                'durationSeconds': summary.durationSeconds, 'reps': summary.exercise_counts(),
                'source': 'webcam', 'completionReason': 'timer', 'synthetic': False}], goal)
            matching = next((row for row in history['sessions'] if row['sessionId'] == workout_id), None)
            return {'ok': True, 'matchingWorkout': matching, 'sufficient': bool(
                matching and matching['comparable'] and matching['summaryVerified'] and matching['suitable'])}
        except Exception as exc:
            return _error(exc)

    @server.tool()
    async def publish_recommendation(workout_id: str, goal_version: str,
                                     recommendation: dict[str, Any]) -> dict[str, Any]:
        """Publish exactly one readback-verified recommendation for a workout and goal version."""
        if recommendation.get('sourceWorkoutId') != workout_id or recommendation.get('goalVersion') != goal_version:
            return {'ok': False, 'error': {'code': 'recommendation_identity_mismatch',
                    'message': 'Recommendation identity does not match its request.', 'status_code': 422,
                    'retryable': False}}
        try:
            key = json.dumps([workout_id, goal_version], separators=(',', ':'))
            created = await client().create_named_recommendation(
                config.recommendations_resource, config.cindypt_origin, key, recommendation)
            return {'ok': True, 'created': created, 'duplicate': not created}
        except Exception as exc:
            return _error(exc)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description='Run the optional CindySense oneM2M MCP server.')
    parser.add_argument('--transport', choices=('stdio',), default='stdio')
    parser.parse_args()
    create_onem2m_mcp_server(OneM2MToolConfig.from_settings()).run(transport='stdio')


if __name__ == '__main__':
    main()
