"""Opt-in MEC 028 subscription management.

Stage I intentionally permits read operations only. Mutation methods fail closed
unless explicitly enabled by a future controlled-live configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import secrets

from .mec_registry import MecServiceRegistry, MecServiceSelection
from .mec_notifications import MecNotificationReceiver
from .mec_schemas import MecAssocStaSubscription


class MecSubscriptionErrorCode(StrEnum):
    DISABLED = 'subscriptions_disabled'
    MUTATIONS_DISABLED = 'subscription_mutations_disabled'
    INVALID_CONFIGURATION = 'invalid_subscription_configuration'
    INVALID_REQUEST = 'invalid_subscription_request'
    HTTP_ERROR = 'subscription_http_error'
    TIMEOUT = 'subscription_timeout'
    CONNECTION_ERROR = 'subscription_connection_error'
    INVALID_CONTENT_TYPE = 'subscription_invalid_content_type'
    INVALID_JSON = 'subscription_invalid_json'
    INVALID_RESPONSE = 'subscription_invalid_response'
    EXPIRED = 'subscription_expired'


class MecSubscriptionError(Exception):
    def __init__(self, code: MecSubscriptionErrorCode | str, message: str,
                 status_code: int, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.message = str(code), message
        self.status_code, self.retryable = status_code, retryable

    def as_dict(self) -> dict[str, Any]:
        return {'code': self.code, 'message': self.message,
                'status_code': self.status_code, 'retryable': self.retryable}


@dataclass(frozen=True)
class MecSubscriptionConfig:
    enabled: bool = False
    callback_public_base_url: str = ''
    callback_token: str = ''
    timeout_seconds: float = 10.0
    allow_mutations: bool = False

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_CONFIGURATION,
                                       'Subscription timeout must be positive.', 503)
        if self.callback_public_base_url:
            parsed = urlsplit(self.callback_public_base_url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_CONFIGURATION,
                                           'Callback base URL must be credential-free HTTPS.', 503)


@dataclass(frozen=True)
class MecSubscriptionRequest:
    callback_reference: str
    bssid: str
    request_test_notification: bool = False
    expiry_deadline: dict[str, Any] | None = None
    notification_event: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'subscriptionType': 'AssocStaSubscription',
            'callbackReference': self.callback_reference,
            'apId': {'bssid': self.bssid.upper()},
            'requestTestNotification': self.request_test_notification,
        }
        if self.expiry_deadline is not None:
            payload['expiryDeadline'] = self.expiry_deadline
        if self.notification_event is not None:
            payload['notificationEvent'] = self.notification_event
        # Pydantic performs the same validation used for inbound payloads.
        return MecAssocStaSubscription.model_validate(payload).model_dump(by_alias=True, exclude_none=True)


@dataclass(frozen=True)
class PendingMecSubscription:
    callback_token: str
    callback_reference: str
    request: MecSubscriptionRequest


class MecSubscriptionLifecycleError(MecSubscriptionError):
    pass


class MecSubscriptionLifecycle:
    """Coordinates callback binding activation around an explicit MEC create."""

    def __init__(self, client: 'MecSubscriptionClient', receiver: MecNotificationReceiver,
                 callback_public_base_url: str, *, token_factory=secrets.token_urlsafe):
        parsed = urlsplit(callback_public_base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment):
            raise MecSubscriptionLifecycleError(
                MecSubscriptionErrorCode.INVALID_CONFIGURATION,
                'Callback base URL must be a credential-free HTTPS URL.', 503)
        self.client = client
        self.receiver = receiver
        self.callback_public_base_url = callback_public_base_url.rstrip('/')
        self._token_factory = token_factory

    def prepare_assoc_sta(self, bssid: str, *, request_test_notification: bool = True,
                          expiry_deadline: dict[str, Any] | None = None,
                          notification_event: dict[str, Any] | None = None) -> PendingMecSubscription:
        token = self._token_factory(32)
        callback = f'{self.callback_public_base_url}/api/mec/notifications/{token}'
        request = MecSubscriptionRequest(callback, bssid, request_test_notification,
                                         expiry_deadline, notification_event)
        request.as_payload()
        self.receiver.bindings.add_pending(token)
        return PendingMecSubscription(token, callback, request)

    @staticmethod
    def _returned_identity(payload: dict[str, Any]) -> str | None:
        direct = payload.get('subscriptionId')
        if isinstance(direct, str) and direct and '/' not in direct:
            return direct
        links = payload.get('_links')
        self_link = links.get('self') if isinstance(links, dict) else None
        href = self_link.get('href') if isinstance(self_link, dict) else None
        if isinstance(href, str):
            path = urlsplit(href).path.rstrip('/')
            identity = path.rsplit('/', 1)[-1] if path else ''
            return identity or None
        return None

    async def create_assoc_sta(self, pending: PendingMecSubscription) -> dict[str, Any]:
        try:
            response = await self.client.create_assoc_sta_subscription(pending.request)
            if not isinstance(response, dict):
                raise MecSubscriptionLifecycleError(
                    MecSubscriptionErrorCode.INVALID_RESPONSE,
                    'MEC subscription creation response must be an object.', 502)
            identity = self._returned_identity(response)
            callback = response.get('callbackReference')
            if not identity or callback != pending.callback_reference:
                raise MecSubscriptionLifecycleError(
                    MecSubscriptionErrorCode.INVALID_RESPONSE,
                    'MEC subscription identity or callback reference was not verified.', 502)
            self.receiver.bindings.activate(pending.callback_token, identity)
            return response
        except Exception:
            self.receiver.bindings.revoke_pending(pending.callback_token)
            raise

    def revoke(self, subscription_id: str) -> bool:
        return self.receiver.revoke_subscription(subscription_id)

    def recover_verified_binding(self, subscription_id: str, callback_token: str,
                                 verified_subscription: dict[str, Any], *,
                                 expires_at: float | None = None) -> None:
        """Rebuild a binding only from a previously GET-verified subscription."""
        expected_callback = f'{self.callback_public_base_url}/api/mec/notifications/{callback_token}'
        if (self._returned_identity(verified_subscription) != subscription_id or
                verified_subscription.get('callbackReference') != expected_callback):
            raise MecSubscriptionLifecycleError(
                MecSubscriptionErrorCode.INVALID_RESPONSE,
                'Verified MEC subscription does not match callback binding.', 502)
        self.receiver.bindings.register(subscription_id, callback_token, expires_at=expires_at)


class MecSubscriptionClient:
    def __init__(self, registry: MecServiceRegistry, config: MecSubscriptionConfig,
                 client: httpx.AsyncClient | None = None, *, clock=lambda: datetime.now(timezone.utc)):
        config.validate()
        self.registry, self.config, self._client, self._clock = registry, config, client, clock

    async def _selection(self) -> MecServiceSelection:
        try:
            return await self.registry.select_wlan()
        except Exception as exc:
            if isinstance(exc, MecSubscriptionError):
                raise
            raise MecSubscriptionError('service_discovery_error',
                                       'MEC WLAN service discovery failed.', 503,
                                       retryable=True) from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds,
                                             verify=True, follow_redirects=False) as client:
                    response = await client.request(method, url, **kwargs)
            else:
                response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise MecSubscriptionError(MecSubscriptionErrorCode.TIMEOUT,
                                       'MEC subscription request timed out.', 504,
                                       retryable=True) from exc
        except httpx.RequestError as exc:
            raise MecSubscriptionError(MecSubscriptionErrorCode.CONNECTION_ERROR,
                                       'MEC subscription service could not be reached.', 503,
                                       retryable=True) from exc
        if not 200 <= response.status_code < 300:
            raise MecSubscriptionError(MecSubscriptionErrorCode.HTTP_ERROR,
                                       f'MEC subscription service returned HTTP {response.status_code}.',
                                       response.status_code, retryable=response.status_code >= 500)
        content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_CONTENT_TYPE,
                                       'MEC subscription service returned non-JSON data.', 502)
        return response

    @staticmethod
    def _subscriptions_url(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_CONFIGURATION,
                                       'Selected MEC endpoint is not a safe HTTPS URL.', 502)
        return urljoin(endpoint.rstrip('/') + '/', 'subscriptions')

    async def list_subscriptions(self, subscription_type: str = 'assoc_sta') -> dict[str, Any]:
        if not self.config.enabled:
            raise MecSubscriptionError(MecSubscriptionErrorCode.DISABLED,
                                       'MEC subscriptions are disabled.', 503)
        selection = await self._selection()
        response = await self._request('GET', self._subscriptions_url(selection.endpoint or ''),
                                       headers={'Accept': 'application/json'},
                                       params={'subscription_type': subscription_type})
        try:
            payload = response.json()
        except ValueError as exc:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_JSON,
                                       'MEC subscription response was malformed.', 502) from exc
        if not isinstance(payload, dict):
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_RESPONSE,
                                       'MEC subscription response must be an object.', 502)
        return payload

    async def create_assoc_sta_subscription(self, request: MecSubscriptionRequest) -> dict[str, Any]:
        if not self.config.enabled:
            raise MecSubscriptionError(MecSubscriptionErrorCode.DISABLED,
                                       'MEC subscriptions are disabled.', 503)
        if not self.config.allow_mutations:
            raise MecSubscriptionError(MecSubscriptionErrorCode.MUTATIONS_DISABLED,
                                       'Subscription mutations are disabled for Stage I.', 403)
        selection = await self._selection()
        payload = request.as_payload()
        response = await self._request('POST', self._subscriptions_url(selection.endpoint or ''),
                                       headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                                       json=payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_JSON,
                                       'MEC subscription creation response was malformed.', 502) from exc
        if not isinstance(data, dict):
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_RESPONSE,
                                       'MEC subscription creation response must be an object.', 502)
        return data

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        if not subscription_id or '/' in subscription_id or '\\' in subscription_id:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_REQUEST,
                                       'Subscription identity is invalid.', 400)
        if not self.config.enabled:
            raise MecSubscriptionError(MecSubscriptionErrorCode.DISABLED,
                                       'MEC subscriptions are disabled.', 503)
        selection = await self._selection()
        url = urljoin(self._subscriptions_url(selection.endpoint or '') + '/', subscription_id)
        response = await self._request('GET', url, headers={'Accept': 'application/json'})
        try:
            data = response.json()
        except ValueError as exc:
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_JSON,
                                       'MEC subscription response was malformed.', 502) from exc
        if not isinstance(data, dict):
            raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_RESPONSE,
                                       'MEC subscription response must be an object.', 502)
        expiry = data.get('expiryDeadline')
        if isinstance(expiry, str):
            try:
                deadline = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
                now = self._clock()
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if now.tzinfo is None:
                    now = now.replace(tzinfo=timezone.utc)
                if deadline <= now.astimezone(timezone.utc):
                    raise MecSubscriptionError(MecSubscriptionErrorCode.EXPIRED,
                                               'MEC subscription has expired.', 410)
            except ValueError as exc:
                raise MecSubscriptionError(MecSubscriptionErrorCode.INVALID_RESPONSE,
                                           'MEC subscription expiry is invalid.', 502) from exc
        return data
