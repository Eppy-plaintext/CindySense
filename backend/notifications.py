"""Validated oneM2M callbacks. The CSE remains the source of confirmed data."""

import asyncio
import json
import logging
import secrets
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError

from .events import classify_repetition_content
from .tinyiot import TinyIoTError

log = logging.getLogger(__name__)


class NotificationHub:
    def __init__(self):
        self.subscriptions = {}
        self.subscription_ids = {}
        self.listeners = set()
        self.seen = set()
        self.seen_order = deque()
        self.validated_callbacks = 0
        self.duplicate_callbacks = 0
        self.rejected_callbacks = 0
        self.sse_queue_drops = 0
        self.instance_id = uuid4().hex
        self.on_summary = None

    def publish(self, kind, data=None):
        for listener in tuple(self.listeners):
            try:
                listener.put_nowait((kind, data or {}))
            except asyncio.QueueFull:
                self.sse_queue_drops += 1
                # The client will reconcile from TinyIoT on reconnect.
                pass

    def metrics(self):
        """Aggregate delivery diagnostics; no tokens, resource contents, or originators."""
        return {'instanceId': self.instance_id,
                'validatedCallbacks': self.validated_callbacks,
                'duplicateCallbacks': self.duplicate_callbacks,
                'rejectedCallbacks': self.rejected_callbacks,
                'sseQueueDrops': self.sse_queue_drops}

    def remember(self, key):
        if key in self.seen:
            return False
        self.seen.add(key)
        self.seen_order.append(key)
        if len(self.seen_order) > 4096:
            self.seen.remove(self.seen_order.popleft())
        return True

    async def ensure(self, client, settings):
        url = settings.notification_url
        token = settings.notification_token
        if not url:
            return {}
        if len(token) < 32 or urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).hostname:
            raise ValueError('Notification URL and a token of at least 32 characters are required.')
        if urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Notification URL must be reachable from TinyIoT inside WSL, not localhost.')
        if urlsplit(url).query or urlsplit(url).fragment or not url.endswith('/api/onem2m/notify/' + token):
            raise ValueError('Notification URL must end with /api/onem2m/notify/<token>.')
        origin = settings.subscription_origin
        if not origin:
            raise ValueError('TINYIOT_SUBSCRIPTION_ORIGIN is required to configure subscriptions.')
        configured = {}
        configured_ids = {}
        for kind, resource, name, legacy_name in (
            ('repetition', settings.repetition_resource, 'cindysense-fastapi-repetitions', 'RepCount_Sub'),
            ('summary', settings.workout_summary_resource, 'cindysense-fastapi-summaries', 'Summary_Sub'),
        ):
            container = await client.retrieve(resource, origin)
            if 'm2m:cnt' not in container:
                raise TinyIoTError('invalid_resource', f'{resource} is not a container.', 502)
            subscriptions = await client.embedded_subscriptions(resource, origin)
            # TinyIoT's embedded CNT representation may omit a named SUB that is
            # still retrievable and delivering notifications. Include both known
            # names so either existing subscription reference is recognized.
            for candidate in (name, legacy_name):
                if any(sub.get('rn') == candidate for sub in subscriptions):
                    continue
                try:
                    payload = await client.retrieve(f'{resource}/{candidate}', origin)
                except TinyIoTError as exc:
                    if exc.code == 'resource_not_found':
                        continue
                    raise
                subscription = payload.get('m2m:sub')
                if isinstance(subscription, dict) and subscription.get('rn') == candidate:
                    subscriptions.append(subscription)
            matched = [f"{resource}/{subscription['rn']}" for subscription in subscriptions
                       if isinstance(subscription.get('rn'), str) and
                       url in subscription.get('nu', []) and
                       3 in (subscription.get('enc') or {}).get('net', [])]
            if matched:
                configured[kind] = matched
                configured_ids[kind] = [sub['ri'] for sub in subscriptions
                                        if isinstance(sub.get('ri'), str) and
                                        f"{resource}/{sub.get('rn')}" in matched]
                continue
            existing = next((sub for sub in subscriptions if sub.get('rn') in (name, legacy_name)), None)
            if existing:
                name = existing['rn']
                await client.update_subscription(f'{resource}/{name}', origin, url)
            else:
                await client.create_subscription(resource, origin, name, url)
            ref = f'{resource}/{name}'
            subscription = next((sub for sub in await client.embedded_subscriptions(resource, origin)
                                 if sub.get('rn') == name), None)
            if not isinstance(subscription, dict) or url not in subscription.get('nu', []) or 3 not in (subscription.get('enc') or {}).get('net', []):
                raise TinyIoTError('subscription_unconfirmed', f'{ref} could not be verified.', 502)
            configured[kind] = [ref]
            configured_ids[kind] = [subscription['ri']] if isinstance(subscription.get('ri'), str) else []
        self.subscriptions = configured
        self.subscription_ids = configured_ids
        return configured

    async def receive(self, body, headers, token, client, settings):
        if not settings.notification_token or not secrets.compare_digest(token, settings.notification_token):
            raise ValueError('invalid notification token')
        rqi = headers.get('x-m2m-ri')
        origin = headers.get('x-m2m-origin')
        rvi = headers.get('x-m2m-rvi')
        missing = [name for name, value in (('X-M2M-RI', rqi), ('X-M2M-Origin', origin),
                                             ('X-M2M-RVI', rvi))
                   if not value or (name == 'X-M2M-RVI' and value not in ('3', '4'))]
        if missing:
            raise ValueError('missing oneM2M request headers: ' + ','.join(missing))
        if settings.notification_origin and origin != settings.notification_origin:
            raise ValueError('unexpected oneM2M origin')
        signal = body.get('m2m:sgn') if isinstance(body, dict) else None
        if not isinstance(signal, dict):
            raise ValueError('missing m2m:sgn')
        if signal.get('vrq') is True:
            return 'verification', rqi
        nev = signal.get('nev')
        if not isinstance(nev, dict) or nev.get('net') not in (3, '3'):
            raise ValueError('invalid notification event type')
        sur = signal.get('sur')
        if not isinstance(sur, str):
            raise ValueError('missing subscription reference')
        normalized_sur = sur.strip('/')
        # TinyIoT sends the subscription reference as /tinyiot/<SUB-RI>, while
        # other CSEs commonly send the structured subscription path. Accept
        # both forms, but require an exact configured subscription RI/path.
        sur_ri = normalized_sur.rsplit('/', 1)[-1]
        matches = [kind for kind, refs in self.subscriptions.items()
                   if any(normalized_sur.endswith('/' + ref) or normalized_sur == ref for ref in refs)
                   or sur_ri in self.subscription_ids.get(kind, [])]
        if len(matches) != 1:
            raise ValueError('unknown subscription reference')
        kind = matches[0]
        rep = nev.get('rep')
        cin = rep.get('m2m:cin') if isinstance(rep, dict) else None
        if not isinstance(cin, dict) or not isinstance(cin.get('rn'), str):
            raise ValueError('missing content instance representation')
        resource = settings.repetition_resource if kind == 'repetition' else settings.workout_summary_resource
        name = cin['rn']
        if not name or '/' in name or name in ('.', '..'):
            raise ValueError('invalid content instance name')
        read_origin = settings.workout_origin if kind == 'repetition' else settings.cindypt_origin
        stored = await client.retrieve(f'{resource}/{name}', read_origin)
        content, instance = client.parse_content_instance(stored)
        if cin.get('ri') and cin['ri'] != instance.get('ri'):
            raise ValueError('content instance identity mismatch')
        if 'con' in cin:
            represented = cin['con']
            if isinstance(represented, str):
                try:
                    represented = json.loads(represented)
                except ValueError:
                    pass
            if represented != content:
                raise ValueError('content instance representation mismatch')
        if kind == 'repetition':
            try:
                classification, event, _ = classify_repetition_content(content)
            except (ValueError, ValidationError) as exc:
                raise ValueError('invalid stored repetition event') from exc
            if classification != 'structured_event' or event is None:
                raise ValueError('notification is not a structured repetition event')
        else:
            from .cindypt import WorkoutSummary
            try:
                summary = WorkoutSummary.model_validate(content)
            except ValidationError as exc:
                raise ValueError('invalid stored workout summary') from exc
            if not isinstance(summary.status, str) or summary.status.lower() not in ('complete', 'completed'):
                raise ValueError('workout summary is not completed')
        key = (kind, instance.get('ri') or f'{resource}/{name}')
        # A CSE can retry the same notification.  Do not invoke the coaching
        # enqueue callback twice for the same stored CIN; the durable SQLite
        # queue is the second idempotency boundary, while this prevents
        # duplicate callback side effects and needless work.
        already_seen = key in self.seen
        if kind == 'summary' and self.on_summary is not None and not already_seen and not (
                isinstance(content, dict) and (content.get('synthetic') is True or
                                               content.get('source') not in (None, 'webcam'))):
            self.on_summary(f'{resource}/{name}', str(key[1]))
        self.validated_callbacks += 1
        if self.remember(key):
            data = {}
            if kind == 'repetition':
                data = {'eventId': str(event.event_id), 'eventSequence': event.event_sequence,
                        'sessionId': event.session_id, 'source': event.source,
                        'timestamp': event.timestamp.isoformat(),
                        'cinCreatedAt': instance.get('ct'),
                        'callbackReceivedAt': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
            self.publish(kind, data)
        else:
            self.duplicate_callbacks += 1
        return kind, rqi
