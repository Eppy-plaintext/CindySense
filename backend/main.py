"""CindySense API: React -> FastAPI -> TinyIoT."""

import secrets
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .cindypt import Goal, analyze, read_goal, read_summaries, recommend
from .events import RepetitionEvent, classify_repetition_content
from .simulation import SimulationPublisher
from .tinyiot import TinyIoTClient, TinyIoTError
from .workout_snapshot import WorkoutSnapshot
from .weekly_plan import PlanPreferences, SavePlanRequest, generate_weekly_plan


@asynccontextmanager
async def lifespan(app):
    yield
    await tinyiot._client.aclose()


app = FastAPI(title='CindySense API', version='0.1.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=False,
    allow_methods=['GET', 'POST'],
    allow_headers=['*'],
)

tinyiot = TinyIoTClient(settings.base_url, httpx.AsyncClient(timeout=5.0))
simulation_publisher = SimulationPublisher()
workout_snapshot = WorkoutSnapshot()




def api_error(exc: TinyIoTError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={'code': exc.code, 'message': str(exc)})


@app.get('/api/health')
async def health():
    try:
        await tinyiot.retrieve('', settings.workout_origin or 'CindySense-health-probe')
    except TinyIoTError as exc:
        if exc.code == 'tinyiot_unreachable':
            return {'backend': {'available': True}, 'tinyiot': {'reachable': False, 'resource_verified': False, 'code': exc.code, 'message': str(exc)}}
        if exc.code not in ('tinyiot_forbidden', 'resource_not_found'):
            return {'backend': {'available': True}, 'tinyiot': {'reachable': True, 'resource_verified': False, 'code': exc.code, 'message': str(exc)}}
    try:
        await tinyiot.retrieve(settings.repetition_resource, settings.workout_origin)
        return {'backend': {'available': True}, 'tinyiot': {'reachable': True, 'resource_verified': True}}
    except TinyIoTError as exc:
        return {'backend': {'available': True}, 'tinyiot': {'reachable': exc.code != 'tinyiot_unreachable', 'resource_verified': False, 'code': exc.code, 'message': str(exc)}}


@app.get('/api/workout/live')
async def live_workout():
    try:
        content, instance = await tinyiot.latest_content(settings.repetition_resource, settings.workout_origin)
        try:
            kind, event, legacy = classify_repetition_content(content)
        except ValueError as exc:
            raise TinyIoTError('invalid_content', 'Latest repetition content is malformed.', 502) from exc
        return {
            'available': True,
            'source': 'TinyIoT',
            'resource_path': settings.repetition_resource,
            'latest_repetition_content': content,
            'content_kind': kind,
            'structured_event': event.content() if event else None,
            'legacy_content': legacy,
            'recorded_at': instance.get('ct'),
        }
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/workout/snapshot')
async def live_workout_snapshot():
    try:
        return await workout_snapshot.read(tinyiot, settings.repetition_resource, settings.workout_origin)
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/workout/webcam-status')
async def webcam_status():
    try:
        content, instance = await tinyiot.latest_content('Webcam_AE/PoseData', settings.webcam_origin)
        if not isinstance(content, dict) or content.get('source') != 'webcam_live':
            raise TinyIoTError('invalid_content', 'Latest webcam pose status is unavailable.', 502)
        return {
            'sessionId': content.get('sessionId'),
            'exercise': content.get('exercise'),
            'detectedRepetitions': content.get('repCount'),
            'eventQueueDepth': content.get('eventQueueDepth'),
            'eventConfirmed': content.get('eventConfirmed'),
            'eventRetries': content.get('eventRetries'),
            'eventOverflow': content.get('eventOverflow'),
            'detectedAt': content.get('timestampUtc'),
            'storedAt': instance.get('ct'),
            'retrievedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
        }
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.post('/api/dev/workout/events')
async def simulate_repetition_event(
    event: RepetitionEvent,
    request: Request,
    x_dev_simulation_token: str | None = Header(default=None),
):
    if not settings.simulation_writes_enabled:
        raise HTTPException(status_code=403, detail={'code': 'simulation_disabled', 'message': 'Simulation writes are disabled.'})
    if request.client is None or request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testserver'):
        raise HTTPException(status_code=403, detail={'code': 'local_only', 'message': 'Simulation writes require a local client.'})
    if len(settings.simulation_token) < 32 or not x_dev_simulation_token or not secrets.compare_digest(x_dev_simulation_token, settings.simulation_token):
        raise HTTPException(status_code=403, detail={'code': 'invalid_simulation_token', 'message': 'Simulation token is missing or invalid.'})
    if not settings.simulation_origin:
        raise HTTPException(status_code=503, detail={'code': 'simulation_origin_not_configured', 'message': 'The separate simulation origin is not configured.'})
    if settings.simulation_origin in (settings.workout_origin, settings.cindypt_origin):
        raise HTTPException(status_code=503, detail={'code': 'simulation_origin_not_separate', 'message': 'The simulation origin must differ from read origins.'})
    if event.source != 'simulator':
        raise HTTPException(status_code=422, detail={'code': 'invalid_source', 'message': 'This endpoint only accepts simulator events.'})
    try:
        created = await simulation_publisher.publish(tinyiot, settings.repetition_resource, settings.simulation_origin, event)
    except TinyIoTError as exc:
        raise api_error(exc) from exc
    return {'created': created, 'duplicate': not created, 'event': event.content(), 'resource_path': f'{settings.repetition_resource}/{event.resource_name}'}


@app.get('/api/workout/history')
async def workout_history():
    return {'available': False, 'source': None, 'sessions': [], 'message': 'Workout history is not connected to a verified oneM2M resource yet.'}


@app.get('/api/cindypt/recommendations')
async def recommendations():
    try:
        try:
            content, instance = await tinyiot.latest_content(settings.recommendations_resource, settings.cindypt_origin)
        except TinyIoTError as exc:
            if exc.code != 'resource_not_found':
                raise
            container = await tinyiot.retrieve(settings.recommendations_resource, settings.cindypt_origin)
            if 'm2m:cnt' not in container:
                raise TinyIoTError('invalid_resource', 'Recommendation resource is not a container.', 502) from exc
            return {'available': False, 'source': 'TinyIoT',
                    'resource_path': settings.recommendations_resource, 'recommendations': None,
                    'recorded_at': None}
        if not isinstance(content, (dict, list, str)):
            raise TinyIoTError('invalid_content', 'Latest recommendation content has an unsupported format.', 502)
        return {
            'available': True,
            'source': 'TinyIoT',
            'resource_path': settings.recommendations_resource,
            'recommendations': content,
            'recorded_at': instance.get('ct'),
        }
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/cindypt/goal')
async def cindypt_goal():
    try:
        goal = await read_goal(tinyiot, settings.target_plan_resource, settings.cindypt_origin)
        return {'available': goal is not None, 'goal': goal, 'source': 'TinyIoT'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.post('/api/cindypt/goal', status_code=201)
async def set_cindypt_goal(goal: Goal):
    try:
        await tinyiot.create_content(settings.target_plan_resource, settings.cindypt_origin, goal.model_dump())
        return {'goal': goal.model_dump(), 'resource_path': settings.target_plan_resource}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


async def _cindypt_progress():
    goal = await read_goal(tinyiot, settings.target_plan_resource, settings.cindypt_origin)
    if goal is None:
        return {'available': False, 'goal': None, 'workout': None, 'message': 'No Cindy goal is stored.'}
    summaries = await read_summaries(tinyiot, settings.workout_summary_resource, settings.cindypt_origin)
    return analyze(goal, summaries[0] if summaries else None)


@app.get('/api/cindypt/progress')
async def cindypt_progress():
    try:
        return await _cindypt_progress()
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.post('/api/cindypt/recommendations/generate', status_code=201)
async def generate_cindypt_recommendation():
    try:
        progress = await _cindypt_progress()
        if not progress['available']:
            raise HTTPException(status_code=409, detail={'code': 'insufficient_data', 'message': progress['message']})
        advice = recommend(progress)
        await tinyiot.create_content(settings.recommendations_resource, settings.cindypt_origin, advice)
        return {'recommendations': advice, 'resource_path': settings.recommendations_resource}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


async def _weekly_preview(preferences: PlanPreferences):
    goal = await read_goal(tinyiot, settings.target_plan_resource, settings.cindypt_origin)
    if goal is None:
        raise HTTPException(status_code=409, detail={'code': 'missing_goal', 'message': 'Save a Cindy goal before planning.'})
    summaries = await read_summaries(tinyiot, settings.workout_summary_resource, settings.cindypt_origin)
    return generate_weekly_plan(goal, summaries, preferences)


@app.post('/api/cindypt/plans/preview')
async def preview_weekly_plan(preferences: PlanPreferences):
    try:
        return {'preview': True, 'plan': await _weekly_preview(preferences)}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.post('/api/cindypt/plans', status_code=201)
async def save_weekly_plan(request: SavePlanRequest):
    try:
        plan = await _weekly_preview(request.preferences)
        if plan['planId'] != request.planId:
            raise HTTPException(status_code=409, detail={'code': 'stale_preview', 'message': 'The plan changed. Preview it again before saving.'})
        resources = (settings.training_plans_resource, settings.workout_schedule_resource,
                     settings.recovery_resource)
        for resource in resources:
            container = await tinyiot.retrieve(resource, settings.cindypt_origin)
            if 'm2m:cnt' not in container:
                raise TinyIoTError('invalid_resource', 'A plan resource is not a oneM2M container.', 502)
        name = 'plan-' + plan['planId']
        records = (plan,
                   {'planId': plan['planId'], 'weekStart': plan['weekStart'],
                    'timezone': plan['timezone'], 'days': plan['days']},
                   {'planId': plan['planId'], 'weekStart': plan['weekStart'],
                    'timezone': plan['timezone'], 'items': plan['recoveryRecommendations']})
        created = []
        for resource, content in zip(resources, records):
            created.append(await tinyiot.create_named_content(resource, settings.cindypt_origin,
                                                               name, content))
        return {'plan': plan, 'alreadySaved': not any(created), 'source': 'TinyIoT'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


async def _latest_optional(resource: str):
    try:
        return await tinyiot.latest_content(resource, settings.cindypt_origin)
    except TinyIoTError as exc:
        if exc.code != 'resource_not_found':
            raise
        container = await tinyiot.retrieve(resource, settings.cindypt_origin)
        if 'm2m:cnt' not in container:
            raise TinyIoTError('invalid_resource', 'A plan resource is not a oneM2M container.', 502) from exc
        return None


@app.get('/api/cindypt/plans')
async def get_saved_weekly_plan():
    try:
        result = await _latest_optional(settings.training_plans_resource)
        if result is None:
            return {'available': False, 'plan': None, 'source': 'TinyIoT'}
        plan, instance = result
        if not isinstance(plan, dict) or not isinstance(plan.get('planId'), str):
            raise TinyIoTError('invalid_content', 'Stored weekly plan is malformed.', 502)
        name = 'plan-' + plan['planId']
        for resource in (settings.workout_schedule_resource, settings.recovery_resource):
            try:
                content, _ = tinyiot.parse_content_instance(
                    await tinyiot.retrieve(f'{resource}/{name}', settings.cindypt_origin))
            except TinyIoTError as exc:
                if exc.code == 'resource_not_found':
                    return {'available': False, 'plan': None, 'incomplete': True, 'source': 'TinyIoT'}
                raise
            if not isinstance(content, dict) or content.get('planId') != plan['planId']:
                raise TinyIoTError('invalid_content', 'Stored weekly plan records disagree.', 502)
        return {'available': True, 'plan': plan, 'recordedAt': instance.get('ct'), 'source': 'TinyIoT'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/cindypt/recovery')
async def get_saved_recovery():
    try:
        result = await _latest_optional(settings.recovery_resource)
        if result is None:
            return {'available': False, 'recovery': None, 'source': 'TinyIoT'}
        content, instance = result
        if not isinstance(content, dict) or not isinstance(content.get('items'), list):
            raise TinyIoTError('invalid_content', 'Stored recovery guidance is malformed.', 502)
        return {'available': True, 'recovery': content, 'recordedAt': instance.get('ct'), 'source': 'TinyIoT'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc
