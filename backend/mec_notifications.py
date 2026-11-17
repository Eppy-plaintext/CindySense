"""Isolated MEC 028 callback receiver with explicit subscription bindings."""

from __future__ import annotations

import hashlib
import asyncio
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .mec_binding_store import MecBindingStateError, MecBindingStateStore, PersistedMecBinding
from .mec_schemas import MecAssocStaNotification, MecTestNotification, parse_mec_notification


@dataclass(frozen=True)
class MecNotificationConfig:
    enabled: bool = False
    callback_token: str = ''
    max_body_bytes: int = 64 * 1024
    notification_container: str = 'MECBridge_AE/WLANNotifications'
    bridge_origin: str = ''
    allowed_subscription_ids: tuple[str, ...] = ()
    pending_ttl_seconds: float = 60.0


@dataclass(frozen=True)
class MecSubscriptionBinding:
    subscription_id: str
    callback_token: str
    expires_at: float | None = None


class MecSubscriptionBindingRegistry:
    """In-memory callbackReference bindings; mutations are explicit and local."""

    def __init__(self, *, clock=time.time, state_store: MecBindingStateStore | None = None):
        self._clock = clock
        self._state_store = state_store
        self._bindings: dict[str, MecSubscriptionBinding] = {}
        self._pending: dict[str, float] = {}

    def _digest(self, callback_token: str) -> str:
        if self._state_store is not None:
            return self._state_store.digest(callback_token)
        return hashlib.sha256(callback_token.encode('utf-8')).hexdigest()

    @property
    def has_persistent_state(self) -> bool:
        return self._state_store is not None

    def add_pending(self, callback_token: str, *, callback_reference: str | None = None,
                    ttl_seconds: float = 60.0) -> None:
        if not callback_token:
            raise ValueError('callback token is required')
        if ttl_seconds <= 0:
            raise ValueError('pending binding TTL must be positive')
        digest = self._digest(callback_token)
        deadline = self._clock() + ttl_seconds
        self._pending[digest] = deadline
        if self._state_store is not None:
            if not callback_reference:
                raise MecBindingStateError('Callback reference is required for persistent pending bindings.')
            self._state_store.upsert_pending(callback_token, callback_reference, deadline)

    def register(self, subscription_id: str, callback_token: str,
                 *, callback_reference: str | None = None,
                 expires_at: float | None = None, persist: bool = True) -> None:
        if not subscription_id or not callback_token:
            raise ValueError('subscription identity and callback token are required')
        digest = self._digest(callback_token)
        self._bindings[digest] = MecSubscriptionBinding(
            subscription_id, callback_token, expires_at)
        self._pending.pop(digest, None)
        if persist and self._state_store is not None:
            if not callback_reference:
                raise MecBindingStateError('Callback reference is required for persistent bindings.')
            self._state_store.upsert(subscription_id, callback_token, callback_reference, expires_at)

    def activate(self, callback_token: str, subscription_id: str,
                 *, callback_reference: str | None = None,
                 expires_at: float | None = None) -> None:
        if not self.is_pending(callback_token):
            raise ValueError('callback token is not pending')
        self.register(subscription_id, callback_token, callback_reference=callback_reference,
                      expires_at=expires_at)

    def revoke_pending(self, callback_token: str) -> bool:
        digest = self._digest(callback_token)
        if digest in self._pending:
            del self._pending[digest]
            if self._state_store is not None:
                self._state_store.remove_token(callback_token)
            return True
        return False

    def is_pending(self, callback_token: str) -> bool:
        self.sync_pending()
        self.cleanup_expired_pending()
        return self._digest(callback_token) in self._pending

    def cleanup_expired_pending(self) -> int:
        now = self._clock()
        expired = [digest for digest, deadline in self._pending.items() if deadline <= now]
        for digest in expired:
            del self._pending[digest]
        if expired and self._state_store is not None:
            self._state_store.remove_pending_digests(set(expired))
        return len(expired)

    def sync_pending(self) -> None:
        if self._state_store is None:
            return
        now = self._clock()
        for item in self._state_store.load():
            if item.status == 'pending' and item.expires_at is not None and item.expires_at > now:
                self._pending[item.token_digest] = item.expires_at

    def clear_active(self) -> None:
        self._bindings.clear()

    def revoke(self, subscription_id: str) -> bool:
        removed = [digest for digest, binding in self._bindings.items()
                   if binding.subscription_id == subscription_id]
        for digest in removed:
            del self._bindings[digest]
        if self._state_store is not None:
            self._state_store.remove(subscription_id)
        return bool(removed)

    def resolve(self, callback_token: str) -> str | None:
        self.cleanup_expired_pending()
        digest = self._digest(callback_token)
        for stored_digest, binding in tuple(self._bindings.items()):
            if not secrets.compare_digest(digest, stored_digest):
                continue
            if binding.expires_at is not None and binding.expires_at <= self._clock():
                del self._bindings[stored_digest]
                if self._state_store is not None:
                    self._state_store.remove(binding.subscription_id)
                return None
            return binding.subscription_id
        return None

    def restore_verified(self, subscription_id: str, callback_token: str,
                         callback_reference: str, *, expires_at: float | None = None) -> None:
        if self._state_store is None:
            raise MecBindingStateError('Persistent binding state is not configured.')
        records = self._state_store.load()
        record = next((item for item in records if item.subscription_id == subscription_id), None)
        if record is None or not self._state_store.matches(record, callback_token, callback_reference):
            raise MecBindingStateError('Verified MEC subscription does not match stored binding state.')
        self.register(subscription_id, callback_token, callback_reference=callback_reference,
                      expires_at=expires_at, persist=False)

    def persisted_bindings(self) -> list[PersistedMecBinding]:
        if self._state_store is None:
            return []
        return self._state_store.load()

    def diagnostics(self) -> dict[str, Any]:
        now = self._clock()
        active = [binding for binding in self._bindings.values()
                  if binding.expires_at is None or binding.expires_at > now]
        self.cleanup_expired_pending()
        return {'registered_count': len(active), 'pending_count': len(self._pending),
                'subscription_ids': sorted(binding.subscription_id for binding in active)}


class MecNotificationReceiver:
    def __init__(self, tinyiot: Any, config: MecNotificationConfig, *, clock=time.time,
                 bindings: MecSubscriptionBindingRegistry | None = None):
        self.tinyiot, self.config, self._clock = tinyiot, config, clock
        self.bindings = bindings or MecSubscriptionBindingRegistry(clock=clock)
        self._seen: dict[str, float] = {}

    def register_subscription(self, subscription_id: str, callback_token: str,
                              *, callback_reference: str | None = None,
                              expires_at: float | None = None) -> None:
        if self.config.allowed_subscription_ids and subscription_id not in self.config.allowed_subscription_ids:
            raise ValueError('subscription identity is not allowlisted')
        self.bindings.register(subscription_id, callback_token,
                               callback_reference=callback_reference,
                               expires_at=expires_at)

    def revoke_subscription(self, subscription_id: str) -> bool:
        return self.bindings.revoke(subscription_id)

    def _result(self, *, accepted: bool, code: str, correlation_id: str,
                event_type: str | None = None, **extra: Any) -> dict[str, Any]:
        return {'accepted': accepted, 'code': code, 'correlation_id': correlation_id,
                'event_type': event_type, **extra}

    def _identity(self, token: str | None) -> str | None:
        if not self.config.enabled or not token:
            return None
        registered_id = self.bindings.resolve(token)
        if registered_id is not None:
            return registered_id
        # The configured token is not trusted by itself. It is only useful after
        # explicit registration, preventing arbitrary client-supplied IDs.
        return None

    async def receive(self, payload: Any, *, token: str | None,
                      subscription_id: str | None, body_size: int | None = None,
                      headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        correlation_id = (headers or {}).get('X-Correlation-ID') or str(uuid.uuid4())
        if not self.config.enabled:
            return self._result(accepted=False, code='notifications_disabled', correlation_id=correlation_id)
        registered_id = self._identity(token)
        pending = bool(token and self.bindings.is_pending(token))
        if registered_id is None and not pending:
            return self._result(accepted=False, code='invalid_callback_credentials', correlation_id=correlation_id)
        if body_size is not None and body_size > self.config.max_body_bytes:
            return self._result(accepted=False, code='body_too_large', correlation_id=correlation_id)
        # subscription_id is optional for the HTTP adapter: MEC 028 does not
        # standardize it in the notification body or a custom header. If a
        # caller supplies one, it must agree with the callbackReference binding.
        if subscription_id is not None and subscription_id != registered_id:
            return self._result(accepted=False, code='unknown_subscription', correlation_id=correlation_id)
        subscription_id = registered_id
        try:
            event = parse_mec_notification(payload)
        except Exception:
            return self._result(accepted=False, code='malformed_or_unsupported_payload', correlation_id=correlation_id)
        event_type = event.notification_type
        if pending:
            if not isinstance(event, MecTestNotification):
                return self._result(accepted=False, code='subscription_pending',
                                    correlation_id=correlation_id, event_type=event_type)
            return self._result(accepted=True, code='pending_test_notification_validated',
                                correlation_id=correlation_id, event_type=event_type,
                                duplicate=False, published=False)
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        event_key = hashlib.sha256(f'{subscription_id}:{canonical}'.encode()).hexdigest()
        now = self._clock()
        if event_key in self._seen:
            return self._result(accepted=True, code='duplicate', correlation_id=correlation_id,
                                event_type=event_type, duplicate=True, published=False)
        if isinstance(event, MecTestNotification):
            self._seen[event_key] = now
            return self._result(accepted=True, code='test_notification_validated',
                                correlation_id=correlation_id, event_type=event_type,
                                duplicate=False, published=False)
        content = {'schema': 'cindysense.mec.wlan.notification.v1',
                   'source': 'ETSI MEC Sandbox simulated network data',
                   'notificationType': event_type, 'subscriptionId': subscription_id,
                   'correlationId': correlation_id, 'notification': payload}
        name = 'mec-notify-' + event_key[:32]
        try:
            verified = await self.tinyiot.create_named_verified_content(
                self.config.notification_container, self.config.bridge_origin, name, content)
        except asyncio.TimeoutError:
            return self._result(accepted=False, code='publication_timeout', correlation_id=correlation_id,
                                event_type=event_type, duplicate=False, published=False, verified=False)
        except Exception:
            return self._result(accepted=False, code='publication_failed', correlation_id=correlation_id,
                                event_type=event_type, duplicate=False, published=False, verified=False)
        if not verified:
            return self._result(accepted=False, code='readback_failed', correlation_id=correlation_id,
                                event_type=event_type, duplicate=False, published=False, verified=False)
        self._seen[event_key] = now
        return self._result(accepted=True, code='published', correlation_id=correlation_id,
                            event_type=event_type, duplicate=False, published=True,
                            verified=True, content_instance_name=name)
