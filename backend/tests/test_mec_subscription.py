import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from backend.mec_registry import MecServiceDiscoveryDiagnostics, MecService, MecServiceSelection
from backend.mec_notifications import MecNotificationConfig, MecNotificationReceiver, MecSubscriptionBindingRegistry
from backend.mec_binding_store import MecBindingStateError, MecBindingStateStore
from backend.mec_subscription import (MecSubscriptionClient, MecSubscriptionConfig,
                                       MecSubscriptionError, MecSubscriptionRequest,
                                       MecSubscriptionLifecycle, MecSubscriptionLifecycleError)


def selection():
    service = MecService('id-1', 'mep1-mec028-1', 'waiId', 'WAI', 'ACTIVE',
                         ('https://mec.test/wai/v2/',))
    return MecServiceSelection(service, 'https://mec.test/wai/v2/', True,
                               MecServiceDiscoveryDiagnostics('discovered', 'mec011', 1,
                                                              'id-1', 'https://mec.test/wai/v2/'))


class MecSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    def test_persistent_state_contains_digests_only_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bindings.json'
            store = MecBindingStateStore(path, 'x' * 32)
            bindings = MecSubscriptionBindingRegistry(state_store=store)
            callback = 'https://callback.test/api/mec/notifications/opaque-token'
            bindings.register('sub-persisted', 'opaque-token', callback_reference=callback)
            raw = path.read_text(encoding='utf-8')
            self.assertNotIn('opaque-token', raw)
            restarted = MecSubscriptionBindingRegistry(state_store=MecBindingStateStore(path, 'x' * 32))
            restarted.restore_verified('sub-persisted', 'opaque-token', callback)
            self.assertEqual(restarted.resolve('opaque-token'), 'sub-persisted')

    def test_corrupt_persistent_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bindings.json'
            path.write_text('{not-json', encoding='utf-8')
            with self.assertRaises(MecBindingStateError):
                MecBindingStateStore(path, 'x' * 32).load()

    def test_pending_binding_expires(self):
        now = [100.0]
        bindings = MecSubscriptionBindingRegistry(clock=lambda: now[0])
        bindings.add_pending('pending', ttl_seconds=5)
        self.assertTrue(bindings.is_pending('pending'))
        now[0] = 106.0
        self.assertFalse(bindings.is_pending('pending'))

    def test_concurrent_state_transactions_preserve_updates_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MecBindingStateStore(Path(directory) / 'bindings.json', 'c' * 32)
            for index in range(10):
                store.upsert(f'initial-{index}', f'token-{index}',
                             f'https://callback.test/{index}', None)

            def add(index):
                store.upsert(f'concurrent-{index}', f'new-token-{index}',
                             f'https://callback.test/new/{index}', None)

            def revoke():
                store.remove('initial-0')

            with ThreadPoolExecutor(max_workers=8) as workers:
                futures = [workers.submit(add, index) for index in range(30)]
                futures.extend(workers.submit(revoke) for _ in range(8))
                for future in futures:
                    future.result()

            records = store.load()
            self.assertEqual(len(records), 39)
            self.assertNotIn('initial-0', {item.subscription_id for item in records})
            self.assertEqual(len({item.subscription_id for item in records}), 39)

    async def test_request_payload_preserves_ap_identity_and_optional_values(self):
        payload = MecSubscriptionRequest(
            'https://callback.test/mec', '005c07070707', True,
            notification_event={'trigger': 1}).as_payload()
        self.assertEqual(payload['subscriptionType'], 'AssocStaSubscription')
        self.assertEqual(payload['apId']['bssid'], '005C07070707')
        self.assertEqual(payload['notificationEvent']['trigger'], 1)

    async def test_list_uses_dynamic_wlan_endpoint(self):
        response = httpx.Response(200, json={'_links': {}}, headers={'content-type': 'application/json'})
        client = AsyncMock()
        client.request.return_value = response
        registry = AsyncMock()
        registry.select_wlan.return_value = selection()
        api = MecSubscriptionClient(registry, MecSubscriptionConfig(enabled=True), client)
        result = await api.list_subscriptions()
        self.assertEqual(result, {'_links': {}})
        self.assertEqual(client.request.await_args.args[:2], ('GET', 'https://mec.test/wai/v2/subscriptions'))

    async def test_mutation_is_disabled_by_default(self):
        api = MecSubscriptionClient(AsyncMock(), MecSubscriptionConfig(enabled=True), AsyncMock())
        with self.assertRaises(MecSubscriptionError) as raised:
            await api.create_assoc_sta_subscription(MecSubscriptionRequest('https://callback.test', '005C07070707'))
        self.assertEqual(raised.exception.code, 'subscription_mutations_disabled')

    async def test_disabled_client_does_not_call_registry(self):
        registry = AsyncMock()
        api = MecSubscriptionClient(registry, MecSubscriptionConfig(enabled=False), AsyncMock())
        with self.assertRaises(MecSubscriptionError):
            await api.list_subscriptions()
        registry.select_wlan.assert_not_awaited()

    async def test_http_error_is_structured(self):
        client = AsyncMock()
        client.request.return_value = httpx.Response(403, json={'error': 'forbidden'},
                                                      headers={'content-type': 'application/json'})
        registry = AsyncMock(); registry.select_wlan.return_value = selection()
        api = MecSubscriptionClient(registry, MecSubscriptionConfig(enabled=True), client)
        with self.assertRaises(MecSubscriptionError) as raised:
            await api.list_subscriptions()
        self.assertEqual(raised.exception.status_code, 403)

    async def test_timeout_is_structured(self):
        client = AsyncMock(); client.request.side_effect = httpx.ReadTimeout('timeout')
        registry = AsyncMock(); registry.select_wlan.return_value = selection()
        api = MecSubscriptionClient(registry, MecSubscriptionConfig(enabled=True), client)
        with self.assertRaises(MecSubscriptionError) as raised:
            await api.list_subscriptions()
        self.assertEqual(raised.exception.code, 'subscription_timeout')

    async def test_lifecycle_handles_immediate_test_notification_before_activation(self):
        tinyiot = AsyncMock(); tinyiot.create_named_verified_content.return_value = True
        receiver = MecNotificationReceiver(
            tinyiot, MecNotificationConfig(True), bindings=MecSubscriptionBindingRegistry())
        mec = AsyncMock()
        lifecycle = MecSubscriptionLifecycle(mec, receiver, 'https://callback.test',
                                              token_factory=lambda _: 'pending-token')
        pending = lifecycle.prepare_assoc_sta('005C07070707')

        async def create(request):
            result = await receiver.receive(
                {'notificationType': 'TestNotification', '_links': {}},
                token=pending.callback_token, subscription_id=None)
            self.assertEqual(result['code'], 'pending_test_notification_validated')
            return {'subscriptionId': 'sub-1', 'callbackReference': request.callback_reference}

        mec.create_assoc_sta_subscription.side_effect = create
        await lifecycle.create_assoc_sta(pending)
        associated = await receiver.receive(
            {'notificationType': 'AssocStaNotification', 'apId': {'bssid': '005C07070707'}},
            token=pending.callback_token, subscription_id=None)
        self.assertEqual(associated['code'], 'published')

    async def test_failed_creation_revokes_pending_binding(self):
        receiver = MecNotificationReceiver(AsyncMock(), MecNotificationConfig(True),
                                           bindings=MecSubscriptionBindingRegistry())
        mec = AsyncMock(); mec.create_assoc_sta_subscription.side_effect = TimeoutError()
        lifecycle = MecSubscriptionLifecycle(mec, receiver, 'https://callback.test',
                                              token_factory=lambda _: 'failed-token')
        pending = lifecycle.prepare_assoc_sta('005C07070707')
        with self.assertRaises(TimeoutError):
            await lifecycle.create_assoc_sta(pending)
        result = await receiver.receive({'notificationType': 'TestNotification', '_links': {}},
                                        token=pending.callback_token, subscription_id=None)
        self.assertEqual(result['code'], 'invalid_callback_credentials')

    async def test_revocation_expiration_and_restart_recovery(self):
        clock = lambda: 100.0
        bindings = MecSubscriptionBindingRegistry(clock=clock)
        receiver = MecNotificationReceiver(AsyncMock(), MecNotificationConfig(True),
                                           clock=clock, bindings=bindings)
        receiver.register_subscription('sub-1', 'token-1', expires_at=99.0)
        expired = await receiver.receive({'notificationType': 'TestNotification', '_links': {}},
                                         token='token-1', subscription_id=None)
        self.assertEqual(expired['code'], 'invalid_callback_credentials')
        receiver.register_subscription('sub-2', 'token-2')
        self.assertTrue(receiver.revoke_subscription('sub-2'))
        restarted = MecNotificationReceiver(AsyncMock(), MecNotificationConfig(True),
                                            bindings=MecSubscriptionBindingRegistry())
        restarted.tinyiot.create_named_verified_content.return_value = True
        lifecycle = MecSubscriptionLifecycle(AsyncMock(), restarted, 'https://callback.test')
        token = 'recovered-token'
        verified = {'subscriptionId': 'sub-3',
                    'callbackReference': f'https://callback.test/api/mec/notifications/{token}',
                    '_links': {'self': {'href': 'https://mec.test/subscriptions/sub-3'}}}
        lifecycle.recover_verified_binding('sub-3', token, verified)
        result = await restarted.receive({'notificationType': 'TestNotification', '_links': {}},
                                         token=token, subscription_id=None)
        self.assertEqual(result['code'], 'test_notification_validated')

    async def test_lifecycle_rejects_unverified_callback_reference(self):
        receiver = MecNotificationReceiver(AsyncMock(), MecNotificationConfig(True),
                                           bindings=MecSubscriptionBindingRegistry())
        lifecycle = MecSubscriptionLifecycle(AsyncMock(), receiver, 'https://callback.test',
                                              token_factory=lambda _: 'bad-token')
        pending = lifecycle.prepare_assoc_sta('005C07070707')
        lifecycle.client.create_assoc_sta_subscription.return_value = {
            'subscriptionId': 'sub-1', 'callbackReference': 'https://attacker.test/callback'}
        with self.assertRaises(MecSubscriptionLifecycleError):
            await lifecycle.create_assoc_sta(pending)

    async def test_invalid_callback_url_is_rejected(self):
        with self.assertRaises(MecSubscriptionError):
            MecSubscriptionClient(AsyncMock(), MecSubscriptionConfig(
                enabled=True, callback_public_base_url='http://callback.test'))

    async def test_expired_subscription_is_reported(self):
        response = httpx.Response(200, json={'subscriptionId': 'sub-1',
                                             'expiryDeadline': '2020-01-01T00:00:00Z'},
                                  headers={'content-type': 'application/json'})
        client = AsyncMock(); client.request.return_value = response
        registry = AsyncMock(); registry.select_wlan.return_value = selection()
        api = MecSubscriptionClient(registry, MecSubscriptionConfig(enabled=True), client)
        with self.assertRaises(MecSubscriptionError) as raised:
            await api.get_subscription('sub-1')
        self.assertEqual(raised.exception.code, 'subscription_expired')


if __name__ == '__main__':
    unittest.main()
