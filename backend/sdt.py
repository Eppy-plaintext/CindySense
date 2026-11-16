"""Custom CindySense SDT-style model and read-only mapping.

This is an application information model inspired by oneM2M TS-0023.  It is
serialized using Annex C's nested JSON shape, but is not a native FCNT
specialization until a CindySense schema is registered in TinyIoT.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .events import RepetitionEvent
from .performance import PerformanceConfig, analyze_performance


class SdtValue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: Any = None
    available: bool = True


class ExerciseTracking(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    exerciseType: Literal['pullup', 'pushup', 'squat'] | None = None
    repetitionCount: int | None = Field(default=None, ge=0)
    completedRounds: int | None = Field(default=None, ge=0)
    detectionSource: Literal['webcam', 'mpu6050', 'manual', 'simulator'] | None = None
    lastRepetitionTime: datetime | None = None


class WorkoutSession(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sessionId: str | None = None
    sessionStatus: Literal['idle', 'running', 'stopped', 'completed', 'unknown'] = 'unknown'
    elapsedSeconds: float | None = Field(default=None, ge=0)
    durationSeconds: float | None = Field(default=None, ge=0)
    startTime: datetime | None = None


class PaceMonitoring(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    performanceState: Literal['insufficient_data', 'stable_performance', 'pace_decreasing', 'unexplained_long_gap', 'unknown'] = 'unknown'
    paceSecondsPerRep: float | None = Field(default=None, ge=0)
    # Signed change: negative means the recent pace is faster than baseline.
    slowdownPercent: float | None = None
    warningStatus: Literal['none', 'active', 'unknown'] = 'unknown'
    latestWarningTime: datetime | None = None

    @field_validator('paceSecondsPerRep', 'slowdownPercent')
    @classmethod
    def finite_pace_values(cls, value):
        if value is not None and not math.isfinite(value):
            raise ValueError('pace values must be finite')
        return value


class Coaching(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    targetRounds: int | None = Field(default=None, ge=0)
    roundsRemaining: int | None = Field(default=None, ge=0)
    recommendation: str | None = None
    coachingStatus: Literal['available', 'unavailable', 'unknown'] = 'unknown'


class CindySenseDevice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    exerciseTracking: ExerciseTracking
    workoutSession: WorkoutSession
    paceMonitoring: PaceMonitoring
    coaching: Coaching


def _source_snapshot(snapshot: dict) -> dict:
    sources = snapshot.get('sources') if isinstance(snapshot, dict) else None
    return sources.get('webcam') if isinstance(sources, dict) and isinstance(sources.get('webcam'), dict) else {}


def _matching_summary(summaries: list[dict] | None, session_id: str | None) -> dict | None:
    if not session_id or not isinstance(summaries, list):
        return None
    return next((item for item in summaries
                 if isinstance(item, dict) and item.get('workoutId') == session_id), None)


def _session_status(worker: dict | None, summary: dict | None) -> str:
    worker_state = worker.get('state') if isinstance(worker, dict) else None
    if worker_state in ('starting', 'running'):
        return 'running'
    if worker_state in ('stopping', 'stopped'):
        if summary and summary.get('completionReason') == 'timer':
            return 'completed'
        return 'stopped'
    if worker_state == 'completed':
        return 'completed'
    if summary is not None:
        return 'completed'
    return 'unknown'


def build_device_sdt(snapshot: dict | None, events: list[RepetitionEvent],
                     *, goal: dict | None = None, recommendation: dict | None = None,
                     warning: dict | None = None, worker: dict | None = None,
                     summaries: list[dict] | None = None) -> dict:
    """Map validated existing sources into a complete, nullable SDT instance."""
    source = _source_snapshot(snapshot or {})
    latest = max(events, key=lambda item: (item.timestamp, str(item.event_id)), default=None)
    worker_active = isinstance(worker, dict) and worker.get('state') in ('starting', 'running')
    worker_session_id = worker.get('sessionId') if worker_active else None
    session_id = worker_session_id or source.get('sessionId') or (latest.session_id if latest else None)
    summary = _matching_summary(summaries, session_id)
    session_events = [event for event in events if event.session_id == session_id] if session_id else []
    performance = analyze_performance(session_events, PerformanceConfig()) if session_events else {}
    exercise = latest.exercise if latest else None
    item = performance.get('exercises', {}).get(exercise, {}) if exercise else {}
    session_state = _session_status(worker, summary)
    start_time = source.get('startAt')
    elapsed = None
    if start_time and latest:
        try:
            elapsed = max(0.0, (latest.timestamp - datetime.fromisoformat(start_time)).total_seconds())
        except (TypeError, ValueError):
            elapsed = None
    counts = source.get('counts') if isinstance(source.get('counts'), dict) else {}
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
        raise ValueError('snapshot counts must be nonnegative integers')
    rounds = source.get('completedRounds')
    target = goal.get('targetRounds') if isinstance(goal, dict) else None
    # The status and percentage must describe the same exercise. The aggregate
    # performance state may be driven by another exercise in the same session.
    pace_state = item.get('state') if item else ('insufficient_data' if not performance else 'unknown')
    pace_warning = warning if isinstance(warning, dict) and warning.get('sessionId') == session_id else None
    mapping_errors = []

    def validate_module(model, values, name):
        try:
            return model.model_validate(values)
        except (TypeError, ValueError) as exc:
            mapping_errors.append({'module': name, 'code': 'invalid_module_data', 'message': str(exc)})
            return model()

    exercise_tracking = validate_module(ExerciseTracking, {
            'exerciseType': exercise,
            'repetitionCount': counts.get(exercise) if exercise else None,
            'completedRounds': rounds if isinstance(rounds, int) else None,
            'detectionSource': latest.source if latest else None,
            'lastRepetitionTime': latest.timestamp if latest else None,
        }, 'exerciseTracking')
    workout_session = validate_module(WorkoutSession, {
            'sessionId': session_id,
            'sessionStatus': session_state,
            'elapsedSeconds': elapsed,
            'durationSeconds': float(summary['durationSeconds']) if summary and isinstance(summary.get('durationSeconds'), (int, float)) else 1200.0,
            'startTime': datetime.fromisoformat(start_time) if isinstance(start_time, str) else None,
        }, 'workoutSession')
    pace_monitoring = validate_module(PaceMonitoring, {
            'performanceState': pace_state if pace_state in ('insufficient_data', 'stable_performance', 'pace_decreasing', 'unexplained_long_gap') else 'unknown',
            'paceSecondsPerRep': item.get('paceSecondsPerRep'),
            'slowdownPercent': item.get('paceChangePercent'),
            'warningStatus': 'active' if pace_warning else ('none' if session_id else 'unknown'),
            'latestWarningTime': (datetime.fromisoformat(pace_warning['timestamp'])
                               if pace_warning and isinstance(pace_warning.get('timestamp'), str) else None),
        }, 'paceMonitoring')
    coaching = validate_module(Coaching, {
            'targetRounds': target if isinstance(target, int) else None,
            'roundsRemaining': max(0, target - rounds) if isinstance(target, int) and isinstance(rounds, int) else None,
            'recommendation': recommendation.get('recommendation') if isinstance(recommendation, dict) else None,
            'coachingStatus': 'available' if recommendation else ('unknown' if session_id is None else 'unavailable'),
        }, 'coaching')
    instance = CindySenseDevice(exerciseTracking=exercise_tracking, workoutSession=workout_session,
                                paceMonitoring=pace_monitoring, coaching=coaching)
    return {
        'schema': 'org.cindysense.device.cindySenseWorkoutTracker/1.0',
        'representation': 'ts-0023-annex-c-json',
        'nativeOneM2MResource': False,
        'deviceIdentity': {'deviceClass': 'cindySenseWorkoutTracker', 'deviceId': 'CindySense_AE'},
        'modules': ['exerciseTracking', 'workoutSession', 'paceMonitoring', 'coaching'],
        'cindySenseWorkoutTracker': instance.model_dump(mode='json', exclude_none=False),
        'mappingErrors': mapping_errors,
    }
