"""CindySense API: React -> FastAPI -> TinyIoT."""

import secrets
import asyncio
import json
import logging
import os
import time
from pathlib import Path
import httpx
from pydantic import ValidationError
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .cindypt import Goal, analyze, read_goal, read_summaries, recommend
from .discovery import CONTAINER_TYPE, DiscoveryStatus, ResourceResolver
from .coaching_selection import select_workout
from .coaching_jobs import CoachingJobs
from .summary_reconciliation import SummaryReconciler
from .events import RepetitionEvent, classify_repetition_content
from .fatigue_history import FatigueHistoryCache
from .federated_learning import read_result
from .notifications import NotificationHub
from .mec_notifications import (MecSubscriptionBindingRegistry,
                                 MecNotificationConfig, MecNotificationReceiver)
from .subscription_monitor import SubscriptionMonitor
from .performance import PaceWarningConfig
from .sdt import build_device_sdt
from .simulation import SimulationPublisher
from .tinyiot import TinyIoTClient, TinyIoTError
from .workout_snapshot import WorkoutSnapshot
from .workout_history import build_history
from .weekly_plan import PlanPreferences, SavePlanRequest, generate_weekly_plan
from .webcam_worker import WebcamWorker


@asynccontextmanager
async def lifespan(app):
    subscription_task = None
    reconciliation_task = None
    coaching_task = asyncio.create_task(coaching_jobs.run(tinyiot, settings, notification_hub.publish))
    if summary_reconciler.enabled:
        reconciliation_task = asyncio.create_task(summary_reconciler.run(tinyiot, settings))
    if subscription_monitor.enabled:
        subscription_task = await subscription_monitor.start(tinyiot, settings)
    try:
        yield
    finally:
        await asyncio.to_thread(webcam_worker.shutdown)
        coaching_task.cancel()
        try:
            await coaching_task
        except asyncio.CancelledError:
            pass
        if subscription_task:
            await subscription_monitor.stop()
        if reconciliation_task:
            reconciliation_task.cancel()
            try:
                await reconciliation_task
            except asyncio.CancelledError:
                pass
        await tinyiot._client.aclose()


app = FastAPI(title='CindySense API', version='0.1.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'http://localhost:5173', 'http://127.0.0.1:5173',
        'http://localhost:5174', 'http://127.0.0.1:5174',
    ],
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['*'],
)

tinyiot = TinyIoTClient(settings.base_url, httpx.AsyncClient(timeout=5.0))
discovery_resolver = ResourceResolver(
    tinyiot, settings.discovery_origin,
    ttl_seconds=getattr(settings, 'discovery_ttl_seconds', 30.0),
)
fatigue_history_cache = FatigueHistoryCache()
simulation_publisher = SimulationPublisher()
workout_snapshot = WorkoutSnapshot()
notification_hub = NotificationHub(PaceWarningConfig(
    slowdown_ratio=settings.pace_warning_slowdown_ratio,
    minimum_pace_intervals=settings.pace_warning_minimum_intervals,
    required_evidence=settings.pace_warning_required_evidence,
    cooldown_seconds=settings.pace_warning_cooldown_seconds,
))
coaching_jobs = CoachingJobs(os.getenv('CINDYSENSE_COACHING_DB', 'data/cindypt_jobs.sqlite3'))
summary_reconciler = SummaryReconciler(
    coaching_jobs, enabled=settings.summary_reconciliation_enabled)
webcam_worker = WebcamWorker()
notification_hub.on_summary = coaching_jobs.enqueue
mec_notification_receiver = MecNotificationReceiver(
    tinyiot,
    MecNotificationConfig(
        enabled=getattr(settings, 'mec_notifications_enabled', False),
        callback_token=getattr(settings, 'mec_callback_token', ''),
        bridge_origin=getattr(settings, 'mec_bridge_origin', ''),
    ),
    bindings=MecSubscriptionBindingRegistry(),
)


def register_verified_subscriptions(required):
    """Make only read-verified subscription references acceptable to callbacks."""
    notification_hub.subscriptions = {
        kind: list(info.get('subscriptions', []))
        for kind, info in required.items()
        if info.get('status') == 'healthy'
    }
    notification_hub.subscription_ids = {
        kind: list(info.get('subscriptionIds', []))
        for kind, info in required.items()
        if info.get('status') == 'healthy'
    }


subscription_monitor = SubscriptionMonitor(
    enabled=getattr(settings, 'subscription_monitor_enabled', True),
    interval_seconds=getattr(settings, 'subscription_monitor_interval_seconds', 60),
    recovery_enabled=getattr(settings, 'subscription_recovery_enabled', True),
    max_backoff_seconds=getattr(settings, 'subscription_monitor_max_backoff_seconds', 300),
    on_verified=register_verified_subscriptions,
    discovery_resolver=discovery_resolver,
)
recommendation_lock = asyncio.Lock()


def _discovery_client():
    """Keep test-injected TinyIoT clients aligned with the shared resolver."""
    discovery_origin = getattr(settings, 'discovery_origin', '')
    if discovery_resolver.client is not tinyiot:
        discovery_resolver.client = tinyiot
        discovery_resolver.invalidate()
    if discovery_resolver.discovery_origin != discovery_origin:
        discovery_resolver.discovery_origin = discovery_origin
        discovery_resolver.invalidate()
    return discovery_resolver


def _discovery_error(result):
    code_by_status = {
        DiscoveryStatus.FORBIDDEN: ('tinyiot_forbidden', 403),
        DiscoveryStatus.MISSING: ('resource_not_found', 404),
        DiscoveryStatus.UNAVAILABLE: ('tinyiot_unreachable', 503),
        DiscoveryStatus.INVALID_RESPONSE: ('invalid_tinyiot_response', 502),
        DiscoveryStatus.UNSUPPORTED: ('tinyiot_error', 502),
        DiscoveryStatus.INVALID_REQUEST: ('invalid_resource_path', 503),
    }
    code, status = code_by_status.get(result.diagnostics.status, ('tinyiot_error', 502))
    return TinyIoTError(code, result.diagnostics.message or 'Resource discovery failed.', status)


async def _resolve_read_resource(ae_scope, name, operation_origin, fallback_path):
    if not operation_origin:
        raise TinyIoTError('origin_not_configured', 'The oneM2M operation originator is not configured.', 503)
    resolver = _discovery_client()
    # The production TinyIoT client always supports discovery.  Keep the
    # configured path usable for narrow injected clients used by isolated
    # integrations that intentionally expose only the retrieve API.
    if not callable(getattr(resolver.client, 'discover', None)):
        return fallback_path
    result = await resolver.resolve(
        ae_scope=ae_scope, name=name, resource_type=CONTAINER_TYPE,
        operation_origin=operation_origin, fallback_path=fallback_path,
    )
    if not result.path:
        raise _discovery_error(result)
    return result.path


async def _resolve_repetition_read_resource():
    """Resolve the dashboard's read-only repetition container safely.

    Event publication deliberately continues to use the configured path: the
    webcam publisher must not gain a discovery dependency or a broader write
    scope.  Reads can use discovery because the configured path is still
    retrieved and type-verified as the safe fallback.
    """
    return await _resolve_read_resource(
        'CindySense_AE', 'RepetitionCount', settings.workout_origin,
        settings.repetition_resource)


def local_webcam_request(request):
    if request.client is None or request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testserver'):
        raise HTTPException(status_code=403, detail='Webcam control and preview require a local client.')


@app.get('/api/workout/webcam-worker')
async def webcam_worker_status(request: Request):
    local_webcam_request(request)
    return webcam_worker.status()


@app.post('/api/workout/webcam-worker/start')
async def start_webcam_worker(request: Request):
    local_webcam_request(request)
    try:
        return webcam_worker.start(event_origin=os.getenv('CINDYSENSE_EVENT_ORIGIN'),
                                   summary_origin=os.getenv('CINDYSENSE_SUMMARY_ORIGIN'),
                                   base_url=settings.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post('/api/workout/webcam-worker/stop')
async def stop_webcam_worker(request: Request):
    local_webcam_request(request)
    return webcam_worker.request_stop()


@app.get('/api/workout/webcam-worker/preview')
async def webcam_worker_preview(request: Request):
    local_webcam_request(request)
    if webcam_worker.status().get('state') not in ('running', 'starting', 'stopping'):
        raise HTTPException(status_code=404, detail='No active webcam preview.')

    async def frames():
        previous = None
        next_status_check = 0.0
        while True:
            if await request.is_disconnected():
                break
            path = webcam_worker.frame_path()
            try:
                frame = path.read_bytes() if path else None
            except OSError:
                frame = None
            if frame and frame != previous:
                previous = frame
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
            now = time.monotonic()
            if now >= next_status_check:
                if webcam_worker.status().get('state') not in ('running', 'starting', 'stopping'):
                    break
                next_status_check = now + 0.5
            # Poll the latest file frequently enough to keep browser frame age
            # below the preview worker interval without retaining a backlog.
            await asyncio.sleep(0.05)

    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame',
                             headers={'Cache-Control': 'no-store'})


@app.post('/api/onem2m/notify/{token}')
async def onem2m_notification(token: str, request: Request):
    notification_hub.record_received()
    if request.headers.get('content-type', '').split(';', 1)[0] not in (
            'application/json', 'application/vnd.onem2m-ntfy+json'):
        notification_hub.record_rejected()
        raise HTTPException(status_code=415, detail='Unsupported notification representation.')
    try:
        body = await request.json()
        kind, rqi = await notification_hub.receive(body, request.headers, token, tinyiot, settings)
    except (ValueError, TypeError) as exc:
        notification_hub.record_rejected()
        logging.getLogger(__name__).warning('Rejected oneM2M notification: %s', exc)
        status = 403 if str(exc) in ('invalid notification token', 'unexpected oneM2M origin') else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except TinyIoTError as exc:
        notification_hub.record_rejected()
        raise api_error(exc) from exc
    return JSONResponse({'m2m:rsp': {'rsc': 2000, 'rqi': rqi}},
                        headers={'X-M2M-RSC': '2000', 'X-M2M-RI': rqi, 'X-M2M-RVI': '4'})


def _refresh_mec_notification_receiver() -> MecNotificationReceiver:
    """Apply current settings without replacing the receiver or its dedupe cache."""
    mec_notification_receiver.tinyiot = tinyiot
    mec_notification_receiver.config = MecNotificationConfig(
        enabled=getattr(settings, 'mec_notifications_enabled', False),
        callback_token=getattr(settings, 'mec_callback_token', ''),
        max_body_bytes=int(getattr(settings, 'mec_callback_max_body_bytes', 64 * 1024)),
        notification_container=getattr(settings, 'mec_notification_container',
                                       'MECBridge_AE/WLANNotifications'),
        bridge_origin=getattr(settings, 'mec_bridge_origin', ''),
        allowed_subscription_ids=tuple(getattr(settings, 'mec_allowed_subscription_ids', ())),
    )
    return mec_notification_receiver


@app.post('/api/mec/notifications/{token}')
async def mec_notification(token: str, request: Request):
    """Receive MEC callbacks through a separate, disabled-by-default contract."""
    receiver = _refresh_mec_notification_receiver()
    max_body_bytes = receiver.config.max_body_bytes
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        raise HTTPException(status_code=415, detail='Unsupported MEC notification representation.')
    declared_length = request.headers.get('content-length')
    try:
        if declared_length is not None and int(declared_length) > max_body_bytes:
            raise HTTPException(status_code=413, detail='MEC notification body is too large.')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid content length.') from exc
    raw_body = await request.body()
    if len(raw_body) > max_body_bytes:
        raise HTTPException(status_code=413, detail='MEC notification body is too large.')
    try:
        body = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail='Malformed MEC notification JSON.') from exc
    result = await receiver.receive(
        body,
        token=token,
        subscription_id=request.headers.get('X-MEC-Subscription-ID'),
        body_size=len(raw_body),
        headers=request.headers,
    )
    status_by_code = {
        'notifications_disabled': 404,
        'invalid_callback_credentials': 401,
        'body_too_large': 413,
        'unknown_subscription': 403,
        'malformed_or_unsupported_payload': 400,
        'publication_timeout': 504,
        'publication_failed': 503,
        'readback_failed': 502,
    }
    status = status_by_code.get(result['code'], 200 if result['accepted'] else 400)
    return JSONResponse(status_code=status, content=result)


@app.get('/api/integration/events')
async def integration_events():
    async def stream():
        listener = asyncio.Queue(maxsize=16)
        notification_hub.listeners.add(listener)
        try:
            yield 'event: reconcile\ndata: {}\n\n'
            while True:
                try:
                    kind, data = await asyncio.wait_for(listener.get(), timeout=15)
                    if kind == 'repetition':
                        data = {**data, 'sseEmittedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
                    yield f'event: {kind}\ndata: {json.dumps(data, separators=(",", ":"))}\n\n'
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            notification_hub.listeners.discard(listener)
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/api/integration/metrics')
async def integration_metrics():
    return {**notification_hub.metrics(), 'coachingJobs': coaching_jobs.metrics(),
            'summaryReconciliation': summary_reconciler.metrics()}


@app.get('/api/fl/status')
async def federated_learning_status():
    """Read the last explicit offline FL run; never starts training."""
    result = read_result(Path(__file__).resolve().parents[1])
    if result is None:
        return {'status': 'not_trained', 'available': False,
                'simulation': 'single-machine-three-virtual-client',
                'message': 'Run the explicit offline FL command to create results.'}
    return {'status': result.get('status', 'unknown'), 'available': True,
            'modelVersion': result.get('modelVersion'), 'provenance': result.get('provenance'),
            'simulation': result.get('simulation', 'single-machine-three-virtual-client'),
            'roundsCompleted': len(result.get('roundMetrics', [])),
            'trainSampleCount': result.get('trainSampleCount', 0),
            'validationSampleCount': result.get('validationSampleCount', 0)}


@app.get('/api/fl/results')
async def federated_learning_results():
    """Read explicit offline FL results without touching workout services."""
    result = read_result(Path(__file__).resolve().parents[1])
    if result is None:
        return {'available': False, 'status': 'not_trained', 'results': None}
    return {'available': True, **result}




def api_error(exc: TinyIoTError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={'code': exc.code, 'message': str(exc)})


@app.get('/api/health')
async def health():
    diagnostics = notification_hub.metrics()
    monitor = subscription_monitor.health()
    try:
        await tinyiot.retrieve(settings.repetition_resource, settings.workout_origin)
        return {'backend': {'available': True},
                'tinyiot': {'reachable': True, 'resource_verified': True},
                'notifications': diagnostics,
                'subscriptionMonitor': monitor,
                'subscriptions': {'configured': bool(notification_hub.subscriptions),
                                  'repetition': notification_hub.subscriptions.get('repetition', []),
                                  'summary': notification_hub.subscriptions.get('summary', [])}}
    except TinyIoTError as exc:
        return {'backend': {'available': True},
                'tinyiot': {'reachable': exc.code != 'tinyiot_unreachable',
                            'resource_verified': False, 'code': exc.code, 'message': str(exc)},
                'notifications': diagnostics,
                'subscriptionMonitor': monitor,
                'subscriptions': {'configured': bool(notification_hub.subscriptions),
                                  'repetition': notification_hub.subscriptions.get('repetition', []),
                                  'summary': notification_hub.subscriptions.get('summary', [])}}


@app.get('/api/onem2m/discovery')
async def onem2m_discovery(label: str | None = Query(default=None, min_length=1, max_length=100)):
    """Discover authorized CindySense containers through the CSE."""
    try:
        origin = settings.discovery_origin
        if not origin:
            raise TinyIoTError('origin_not_configured', 'A discovery originator is not configured.', 503)
        resources = await tinyiot.discover('CindySense_AE', origin, resource_type=3, label=label)
        return {'source': 'TinyIoT', 'discovery': 'CSE', 'resource_path': 'CindySense_AE',
                'resource_type': 3, 'label': label, 'resources': resources,
                'owners': {uri: 'CindySense_AE' for uri in resources}}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/onem2m/discovery/status')
async def onem2m_discovery_status():
    """Return read-only, non-sensitive discovery diagnostics."""
    resolver = _discovery_client()
    discovery_configured = bool(getattr(settings, 'discovery_origin', ''))
    results = await asyncio.gather(
        resolver.discover_aes(),
        resolver.discover_containers('CindySense_AE'),
        resolver.discover_containers('CindyPT_AE'),
        resolver.discover_subscriptions('CindySense_AE', 'RepetitionCount'),
        resolver.discover_subscriptions('CindySense_AE', 'WorkoutSummary'),
    )
    names = ('aes', 'cindySenseContainers', 'cindyPtContainers',
             'repetitionSubscriptions', 'summarySubscriptions')
    serialized = {name: result.as_dict() for name, result in zip(names, results)}
    diagnostics = [result.diagnostics for result in results]
    errors = [item.as_dict() for item in diagnostics
              if item.status != DiscoveryStatus.DISCOVERED]
    return {
        'source': 'TinyIoT',
        'discovery': {
            'configured': discovery_configured,
            'available': discovery_configured and not all(
                item.status in (DiscoveryStatus.FORBIDDEN, DiscoveryStatus.UNAVAILABLE,
                                DiscoveryStatus.ERROR, DiscoveryStatus.INVALID_REQUEST)
                for item in diagnostics),
        },
        'results': serialized,
        'cache': resolver.cache_status(),
        'errors': errors,
        'fallback': {
            'configured': {
                'repetition': bool(settings.repetition_resource),
                'summary': bool(settings.workout_summary_resource),
                'cindyPt': bool(settings.target_plan_resource and settings.recommendations_resource),
                'plans': bool(settings.training_plans_resource and settings.workout_schedule_resource and
                              settings.recovery_resource),
            },
            'used': any(item.diagnostics.status == DiscoveryStatus.FALLBACK for item in results),
        },
    }


@app.get('/api/onem2m/workout-timeseries')
async def workout_timeseries_history():
    """Read bounded workout history directly from TinyIoT; never write or alter CindyPT."""
    origin = settings.workout_ts_read_origin
    resource = settings.workout_ts_resource.strip('/')
    try:
        parent = await tinyiot.retrieve(resource, origin)
        ts = parent.get('m2m:ts')
        if not isinstance(ts, dict) or ts.get('ty') != 29:
            raise TinyIoTError('invalid_tinyiot_response', 'Workout history resource is not a TS.', 502)
        refs = await tinyiot.child_references(resource, origin, resource_type=30)
        prefix = f'TinyIoT/{resource}/'
        if len(refs) > 100 or any(not ref.startswith(prefix) or '/' in ref[len(prefix):]
                                  for ref in refs):
            raise TinyIoTError('invalid_tinyiot_response', 'Workout history discovery is invalid or too large.', 502)
        workouts = []
        for ref in refs:
            record = await tinyiot.retrieve(ref[len('TinyIoT/'):], origin)
            instance = record.get('m2m:tsi')
            if not isinstance(instance, dict):
                raise TinyIoTError('invalid_tinyiot_response', 'Workout history instance is malformed.', 502)
            content = instance.get('con')
            try:
                content = json.loads(content) if isinstance(content, str) else content
            except ValueError as exc:
                raise TinyIoTError('invalid_tinyiot_response', 'Workout history content is not JSON.', 502) from exc
            if not isinstance(content, dict):
                raise TinyIoTError('invalid_tinyiot_response', 'Workout history content is malformed.', 502)
            workouts.append({'resource_path': ref[len('TinyIoT/'):],
                             'dataGenerationTime': instance.get('dgt'), 'content': content})
        return {'source': 'TinyIoT', 'resource_path': resource, 'count': len(workouts),
                'workouts': workouts}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/onem2m/group/workout-monitoring')
async def workout_monitoring_group():
    """Read the configured monitoring GRP and its members through fan-out."""
    try:
        if not settings.group_origin:
            raise TinyIoTError('origin_not_configured', 'A group originator is not configured.', 503)
        members = await tinyiot.group_members(settings.group_resource, settings.group_origin)
        fanout = await tinyiot.fanout_retrieve(settings.group_resource, settings.group_origin)
        aggregate = fanout.get('m2m:agr')
        responses = aggregate.get('m2m:rsp') if isinstance(aggregate, dict) else None
        if isinstance(responses, dict):
            responses = [responses]
        if not isinstance(responses, list) or len(responses) != len(members):
            raise TinyIoTError('invalid_tinyiot_response', 'Fan-out response count does not match the group.', 502)
        statuses = [item.get('rsc') for item in responses if isinstance(item, dict)]
        if len(statuses) != len(members):
            raise TinyIoTError('invalid_tinyiot_response', 'Fan-out status is malformed.', 502)
        overall = 'success' if all(str(status) == '2000' for status in statuses) else 'partial_failure'
        return {'source': 'TinyIoT', 'resource_path': settings.group_resource,
                'members': members, 'fanout': fanout, 'fanout_results': responses,
                'member_statuses': statuses,
                'overall_status': overall}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/workout/live')
async def live_workout():
    try:
        resource = await _resolve_repetition_read_resource()
        content, instance = await tinyiot.latest_content(resource, settings.workout_origin)
        try:
            kind, event, legacy = classify_repetition_content(content)
        except ValueError as exc:
            raise TinyIoTError('invalid_content', 'Latest repetition content is malformed.', 502) from exc
        return {
            'available': True,
            'source': 'TinyIoT',
            'resource_path': resource,
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
        resource = await _resolve_repetition_read_resource()
        return await workout_snapshot.read(tinyiot, resource, settings.workout_origin)
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/sdt/device')
async def sdt_device():
    """Return a read-only custom CindySense SDT-style device representation."""
    errors = []
    try:
        snapshot = await workout_snapshot.read(tinyiot, settings.repetition_resource, settings.workout_origin)
        events = workout_snapshot.validated_events()
    except TinyIoTError as exc:
        snapshot, events = None, []
        errors.append({'source': 'RepetitionCount', 'code': exc.code})

    goal = recommendation = None
    summaries = None
    worker = await asyncio.to_thread(webcam_worker.status)
    if settings.cindypt_origin:
        try:
            goal_resource = await _resolve_read_resource(
                'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
                settings.target_plan_resource)
            goal = await read_goal(tinyiot, goal_resource, settings.cindypt_origin)
        except TinyIoTError as exc:
            errors.append({'source': 'FitnessGoals', 'code': exc.code})
        try:
            summary_resource = await _resolve_read_resource(
                'CindySense_AE', 'WorkoutSummary', settings.cindypt_origin,
                settings.workout_summary_resource)
            summaries = await read_summaries(tinyiot, summary_resource, settings.cindypt_origin)
        except TinyIoTError as exc:
            errors.append({'source': 'WorkoutSummary', 'code': exc.code})
        try:
            recommendation_resource = await _resolve_read_resource(
                'CindyPT_AE', 'AIRecommendations', settings.cindypt_origin,
                settings.recommendations_resource)
            recommendation, _ = await tinyiot.latest_content(recommendation_resource, settings.cindypt_origin)
            if not isinstance(recommendation, dict):
                recommendation = None
        except TinyIoTError as exc:
            errors.append({'source': 'AIRecommendations', 'code': exc.code})

    try:
        representation = build_device_sdt(snapshot, events, goal=goal, recommendation=recommendation,
                                          worker=worker, summaries=summaries)
    except (TypeError, ValueError) as exc:
        errors.append({'source': 'mapping', 'code': 'invalid_source_data', 'message': str(exc)})
        representation = build_device_sdt(None, [])
    representation['available'] = snapshot is not None
    representation['errors'] = errors
    return representation


@app.get('/api/workout/fatigue')
async def workout_fatigue(sessionId: str | None = Query(default=None, min_length=1, max_length=100)):
    """Calculate non-medical fatigue signals from one validated retained session."""
    try:
        history = await fatigue_history_cache.read(
            tinyiot, settings.repetition_resource, settings.workout_origin,
            selected_session_id=sessionId)
        return {
            'available': history['selectedSessionId'] is not None,
            'source': 'validated TinyIoT repetition history',
            'dataSufficient': history['dataSufficient'],
            'cachedEventCount': history['selectedEventCount'],
            **history,
            'medicalDisclaimer': 'Heuristic performance signal; not medically validated fatigue detection.',
        }
    except TinyIoTError as exc:
        # A transient history read must not turn into an apparently valid
        # fatigue result. Keep the dashboard explicit about unavailability.
        return {
            'available': False,
            'source': 'validated TinyIoT repetition history',
            'dataSufficient': False,
            'cachedEventCount': 0,
            'retainedInstanceCount': None,
            'retrievedInstanceCount': 0,
            'validEventCount': 0,
            'excludedEventCount': 0,
            'selectedSessionId': sessionId,
            'availableSessions': [],
            'exclusionReasons': {'history_unavailable': 1},
            'selectionError': 'history_unavailable',
            'selectedEventCount': 0,
            'results': [],
            'medicalDisclaimer': 'Heuristic performance signal; not medically validated fatigue detection.',
            'message': 'Fatigue history is temporarily unavailable; no estimate was produced.',
            'errorCode': exc.code,
        }


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
    try:
        observed = await fatigue_history_cache.read(tinyiot, settings.repetition_resource, settings.workout_origin)
        summary_resource = await _resolve_read_resource(
            'CindySense_AE', 'WorkoutSummary', settings.cindypt_origin,
            settings.workout_summary_resource)
        goal_resource = await _resolve_read_resource(
            'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
            settings.target_plan_resource)
        summaries = await read_summaries(tinyiot, summary_resource, settings.cindypt_origin)
        goal = await read_goal(tinyiot, goal_resource, settings.cindypt_origin)
        return build_history(observed, summaries, goal)
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/workout/performance')
async def workout_performance(sessionId: str | None = Query(default=None, min_length=1, max_length=100)):
    try:
        observed = await fatigue_history_cache.read(
            tinyiot, settings.repetition_resource, settings.workout_origin,
            selected_session_id=sessionId)
        selected = next((item for item in observed['availableSessions']
                         if item['sessionId'] == observed['selectedSessionId']), None)
        return {'available': selected is not None,
                'sessionId': observed['selectedSessionId'],
                'performance': selected['performance'] if selected else None,
                'selectionError': observed['selectionError'],
                'source': 'validated retained TinyIoT repetition events'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/cindypt/recommendations')
async def recommendations():
    try:
        resource = await _resolve_read_resource(
            'CindyPT_AE', 'AIRecommendations', settings.cindypt_origin,
            settings.recommendations_resource)
        try:
            content, instance = await tinyiot.latest_content(resource, settings.cindypt_origin)
        except TinyIoTError as exc:
            if exc.code != 'resource_not_found':
                raise
            container = await tinyiot.retrieve(resource, settings.cindypt_origin)
            if 'm2m:cnt' not in container:
                raise TinyIoTError('invalid_resource', 'Recommendation resource is not a container.', 502) from exc
            return {'available': False, 'source': 'TinyIoT',
                    'resource_path': resource, 'recommendations': None,
                    'recorded_at': None}
        if not isinstance(content, (dict, list, str)):
            raise TinyIoTError('invalid_content', 'Latest recommendation content has an unsupported format.', 502)
        return {
            'available': True,
            'source': 'TinyIoT',
            'resource_path': resource,
            'recommendations': content,
            'recorded_at': instance.get('ct'),
        }
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/cindypt/goal')
async def cindypt_goal():
    try:
        resource = await _resolve_read_resource(
            'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
            settings.target_plan_resource)
        goal = await read_goal(tinyiot, resource, settings.cindypt_origin)
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
    goal_resource = await _resolve_read_resource(
        'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
        settings.target_plan_resource)
    goal = await read_goal(tinyiot, goal_resource, settings.cindypt_origin)
    if goal is None:
        return {'available': False, 'goal': None, 'workout': None, 'latestWorkout': None,
                'coachingWorkout': None, 'selectionStatus': 'insufficient_data',
                'message': 'No Cindy goal is stored.'}
    summary_resource = await _resolve_read_resource(
        'CindySense_AE', 'WorkoutSummary', settings.cindypt_origin,
        settings.workout_summary_resource)
    summaries = await read_summaries(tinyiot, summary_resource, settings.cindypt_origin)
    observed = await fatigue_history_cache.read(tinyiot, settings.repetition_resource, settings.workout_origin)
    return select_workout(build_history(observed, summaries, goal), summaries, goal)


@app.get('/api/cindypt/progress')
async def cindypt_progress():
    try:
        return await _cindypt_progress()
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.get('/api/cindypt/recommendations/current')
async def current_cindypt_recommendation():
    """Preview advice for the latest completed summary without changing saved plans."""
    try:
        progress = await _cindypt_progress()
        if not progress['available']:
            return {'available': False, 'recommendations': None,
                    'message': progress.get('selectionReason') or progress.get('message')}
        return {'available': True, 'recommendations': recommend(progress), 'source': 'rule-based-preview'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


@app.post('/api/cindypt/recommendations/generate', status_code=201)
async def generate_cindypt_recommendation():
    try:
        async with recommendation_lock:
            progress = await _cindypt_progress()
            if not progress['available'] or progress.get('selectionStatus') != 'sufficient':
                raise HTTPException(status_code=409, detail={'code': progress.get('selectionStatus', 'insufficient_data'),
                                                             'message': progress.get('selectionReason') or progress.get('message')})
            advice = recommend(progress)
            goal_resource = await _resolve_read_resource(
                'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
                settings.target_plan_resource)
            goal_content, goal_instance = await tinyiot.latest_content(goal_resource,
                                                                        settings.cindypt_origin)
            try:
                current_goal = Goal.model_validate(goal_content).model_dump()
            except ValidationError as exc:
                raise TinyIoTError('invalid_content', 'Stored goal is malformed.', 502) from exc
            if current_goal != progress['goal']:
                raise HTTPException(status_code=409, detail={'code': 'stale_goal', 'message': 'The goal changed. Retry with the latest goal.'})
            goal_version = str(goal_instance.get('ri') or goal_instance.get('ct') or '')
            if not goal_version:
                raise TinyIoTError('goal_version_missing', 'Goal has no stable version.', 502)
            advice['goalVersion'] = goal_version
            key = json.dumps([progress['workout']['workoutId'], goal_version], separators=(',', ':'))
            created = await tinyiot.create_named_recommendation(settings.recommendations_resource,
                                                                 settings.cindypt_origin, key, advice)
            return {'recommendations': advice, 'resource_path': settings.recommendations_resource,
                    'alreadySaved': not created}
    except TinyIoTError as exc:
        raise api_error(exc) from exc


async def _weekly_preview(preferences: PlanPreferences):
    goal_resource = await _resolve_read_resource(
        'CindyPT_AE', 'FitnessGoals', settings.cindypt_origin,
        settings.target_plan_resource)
    summary_resource = await _resolve_read_resource(
        'CindySense_AE', 'WorkoutSummary', settings.cindypt_origin,
        settings.workout_summary_resource)
    goal = await read_goal(tinyiot, goal_resource, settings.cindypt_origin)
    if goal is None:
        raise HTTPException(status_code=409, detail={'code': 'missing_goal', 'message': 'Save a Cindy goal before planning.'})
    summaries = await read_summaries(tinyiot, summary_resource, settings.cindypt_origin)
    observed = await fatigue_history_cache.read(tinyiot, settings.repetition_resource, settings.workout_origin)
    history = build_history(observed, summaries, goal)
    qualified = {row['sessionId'] for row in history['sessions'] if row['comparable'] and row['summaryVerified']
                 and row['eventCount'] >= 30 and row['suitable']}
    return generate_weekly_plan(goal, [item for item in summaries if item['workoutId'] in qualified], preferences)


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
        training_resource = await _resolve_read_resource(
            'CindyPT_AE', 'TrainingPlans', settings.cindypt_origin,
            settings.training_plans_resource)
        result = await _latest_optional(training_resource)
        if result is None:
            return {'available': False, 'plan': None, 'source': 'TinyIoT'}
        plan, instance = result
        if not isinstance(plan, dict) or not isinstance(plan.get('planId'), str):
            raise TinyIoTError('invalid_content', 'Stored weekly plan is malformed.', 502)
        name = 'plan-' + plan['planId']
        related_resources = (
            await _resolve_read_resource('CindyPT_AE', 'WorkoutSchedule', settings.cindypt_origin,
                                         settings.workout_schedule_resource),
            await _resolve_read_resource('CindyPT_AE', 'RecoveryRecommendations', settings.cindypt_origin,
                                         settings.recovery_resource),
        )
        for resource in related_resources:
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
        recovery_resource = await _resolve_read_resource(
            'CindyPT_AE', 'RecoveryRecommendations', settings.cindypt_origin,
            settings.recovery_resource)
        result = await _latest_optional(recovery_resource)
        if result is None:
            return {'available': False, 'recovery': None, 'source': 'TinyIoT'}
        content, instance = result
        if not isinstance(content, dict) or not isinstance(content.get('items'), list):
            raise TinyIoTError('invalid_content', 'Stored recovery guidance is malformed.', 502)
        return {'available': True, 'recovery': content, 'recordedAt': instance.get('ct'), 'source': 'TinyIoT'}
    except TinyIoTError as exc:
        raise api_error(exc) from exc
