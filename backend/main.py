"""CindySense API: React -> FastAPI -> TinyIoT."""

import secrets

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .events import RepetitionEvent, classify_repetition_content
from .simulation import SimulationPublisher
from .tinyiot import TinyIoTClient, TinyIoTError


app = FastAPI(title='CindySense API', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=False,
    allow_methods=['GET'],
    allow_headers=['*'],
)

tinyiot = TinyIoTClient(settings.base_url)
simulation_publisher = SimulationPublisher()


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
        content, instance = await tinyiot.latest_content(settings.recommendations_resource, settings.cindypt_origin)
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
