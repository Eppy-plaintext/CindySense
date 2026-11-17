"""Callback-only ASGI application for controlled MEC notification testing.

Run this app separately from the main CindySense API and bind it to loopback.
The application intentionally has no health, docs, dashboard, workout, or admin
routes. Uvicorn access logging should be disabled when launching it because the
callback token is part of the URL path.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import settings
from .mec_binding_store import MecBindingStateError, MecBindingStateStore
from .mec_notifications import (MecNotificationConfig, MecNotificationReceiver,
                                MecSubscriptionBindingRegistry)
from .mec_registry import MecServiceDiscoveryConfig, MecServiceRegistry
from .mec_subscription import (MecSubscriptionClient, MecSubscriptionConfig,
                               MecSubscriptionLifecycle)
from .tinyiot import TinyIoTClient


async def _handle_callback(receiver: MecNotificationReceiver, token: str,
                           request: Request) -> JSONResponse:
    if not receiver.config.enabled:
        return JSONResponse({'accepted': False, 'code': 'notifications_disabled'}, status_code=404)
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        raise HTTPException(status_code=415, detail='Unsupported MEC notification representation.')
    maximum = receiver.config.max_body_bytes
    declared = request.headers.get('content-length')
    try:
        if declared is not None and int(declared) > maximum:
            raise HTTPException(status_code=413, detail='MEC notification body is too large.')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid content length.') from exc
    raw = await request.body()
    if len(raw) > maximum:
        raise HTTPException(status_code=413, detail='MEC notification body is too large.')
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail='Malformed MEC notification JSON.') from exc
    result = await receiver.receive(
        payload,
        token=token,
        subscription_id=request.headers.get('X-MEC-Subscription-ID'),
        body_size=len(raw),
        headers=request.headers,
    )
    status_by_code = {
        'notifications_disabled': 404,
        'invalid_callback_credentials': 401,
        'body_too_large': 413,
        'unknown_subscription': 403,
        'malformed_or_unsupported_payload': 400,
        'subscription_pending': 409,
        'publication_timeout': 504,
        'publication_failed': 503,
        'readback_failed': 502,
    }
    status = status_by_code.get(result['code'], 200 if result['accepted'] else 400)
    return JSONResponse(status_code=status, content=result)


def _callback_token_from_reference(callback_reference: str) -> str | None:
    parsed = urlsplit(callback_reference)
    marker = '/api/mec/notifications/'
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if marker not in parsed.path:
        return None
    token = parsed.path.split(marker, 1)[1].strip('/')
    return token if token and '/' not in token else None


class VerifiedBindingRecovery:
    """Rehydrates only records that still match a live MEC subscription."""

    def __init__(self, receiver: MecNotificationReceiver, lifecycle: MecSubscriptionLifecycle,
                 store: MecBindingStateStore, client: MecSubscriptionClient):
        self.receiver, self.lifecycle, self.store, self.client = receiver, lifecycle, store, client

    async def __call__(self) -> dict[str, int]:
        restored = 0
        rejected = 0
        self.receiver.bindings.clear_active()
        self.receiver.bindings.sync_pending()
        records = self.store.load()
        for record in records:
            if record.status != 'active' or not record.subscription_id:
                continue
            try:
                remote = await self.client.get_subscription(record.subscription_id)
                if remote.get('state') not in (None, 'ACTIVE'):
                    raise MecBindingStateError('MEC subscription is not active.')
                callback = remote.get('callbackReference')
                token = _callback_token_from_reference(callback) if isinstance(callback, str) else None
                identity = self.lifecycle._returned_identity(remote)
                if not token or identity != record.subscription_id:
                    raise MecBindingStateError('MEC subscription identity or callback reference is invalid.')
                expiry = remote.get('expiryDeadline')
                expires_at = None
                if isinstance(expiry, str):
                    deadline = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
                    if deadline.tzinfo is None:
                        deadline = deadline.replace(tzinfo=timezone.utc)
                    expires_at = deadline.timestamp()
                self.receiver.bindings.restore_verified(
                    record.subscription_id, token, callback, expires_at=expires_at)
                restored += 1
            except Exception:
                # Do not activate a stale or unverifiable binding. Keep its
                # encrypted-digest record for a later reconciliation attempt
                # when the remote service is temporarily unavailable.
                rejected += 1
        return {'restored': restored, 'rejected': rejected}


def create_callback_app(receiver: MecNotificationReceiver,
                        recovery: Callable[[], Awaitable[dict[str, int]]] | None = None,
                        close: Callable[[], Awaitable[None]] | None = None) -> FastAPI:
    recovery_result: dict[str, int] = {'restored': 0, 'rejected': 0}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        receiver.bindings.cleanup_expired_pending()
        if recovery is not None and receiver.config.enabled:
            try:
                recovery_result.update(await recovery())
            except Exception:
                # Startup remains available but all unverified bindings remain
                # absent from memory, so callbacks fail closed.
                recovery_result['rejected'] += 1
        app.state.mec_recovery = dict(recovery_result)
        try:
            yield
        finally:
            if close is not None:
                await close()

    app = FastAPI(title='CindySense MEC Callback Receiver', docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.post('/api/mec/notifications/{token}')
    async def mec_notification(token: str, request: Request):
        if recovery is not None and receiver.config.enabled:
            try:
                await recovery()
            except Exception:
                # Recovery failure never promotes an unverified active binding.
                receiver.bindings.clear_active()
                receiver.bindings.sync_pending()
        return await _handle_callback(receiver, token, request)

    return app


def _build_default_app() -> FastAPI:
    store = None
    if settings.mec_callback_binding_key:
        try:
            store = MecBindingStateStore(settings.mec_callback_binding_state_path,
                                         settings.mec_callback_binding_key)
        except MecBindingStateError:
            store = None
    registry = MecSubscriptionBindingRegistry(state_store=store)
    receiver = MecNotificationReceiver(
        TinyIoTClient(settings.base_url, httpx.AsyncClient(timeout=5.0)),
        MecNotificationConfig(
            enabled=settings.mec_notifications_enabled,
            bridge_origin=settings.mec_bridge_origin,
            pending_ttl_seconds=settings.mec_callback_pending_ttl_seconds,
        ),
        bindings=registry,
    )
    recovery = None
    close_client = receiver.tinyiot._client.aclose
    if store is not None and settings.mec_subscriptions_enabled and settings.mec_service_discovery_enabled:
        discovery = MecServiceRegistry(MecServiceDiscoveryConfig(
            enabled=True,
            api_base_url=settings.mec_service_mgmt_api_base_url,
            fallback_wlan_url=settings.mec_wlan_api_base_url,
            ttl_seconds=settings.mec_service_discovery_ttl_seconds,
        ))
        subscription_client = MecSubscriptionClient(discovery, MecSubscriptionConfig(
            enabled=True,
            callback_public_base_url=settings.mec_callback_public_base_url,
            allow_mutations=False,
            timeout_seconds=settings.mec_request_timeout_seconds,
        ))
        lifecycle = MecSubscriptionLifecycle(
            subscription_client, receiver, settings.mec_callback_public_base_url)
        recovery = VerifiedBindingRecovery(receiver, lifecycle, store, subscription_client)

    async def close():
        await close_client()

    return create_callback_app(receiver, recovery=recovery, close=close)


app = _build_default_app()
