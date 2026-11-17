import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from backend.mec_binding_store import MecBindingStateStore
from backend.mec_callback_app import (VerifiedBindingRecovery, create_callback_app)
from backend.mec_notifications import (MecNotificationConfig, MecNotificationReceiver,
                                        MecSubscriptionBindingRegistry)
from backend.mec_subscription import MecSubscriptionLifecycle


TOKEN = 'opaque-local-token'
SUBSCRIPTION = 'mec-sub-local'
ASSOC = {
    'notificationType': 'AssocStaNotification',
    'apId': {'bssid': '005C07070707'},
    'staId': {'macAddress': '001122334455'},
}


class MecCallbackAppTests(unittest.IsolatedAsyncioTestCase):
    def build(self, enabled=True, *, recovery=None):
        tinyiot = AsyncMock()
        tinyiot.create_named_verified_content.return_value = True
        receiver = MecNotificationReceiver(
            tinyiot,
            MecNotificationConfig(enabled=enabled, callback_token='', bridge_origin='CMECBridge'),
            bindings=MecSubscriptionBindingRegistry(),
        )
        receiver.register_subscription(
            SUBSCRIPTION, TOKEN,
            callback_reference='https://callback.test/api/mec/notifications/' + TOKEN,
        )
        return create_callback_app(receiver, recovery=recovery), tinyiot

    async def request(self, app, path, payload=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://callback.test') as client:
            return await client.post(path, json=payload,
                                     headers={'content-type': 'application/json'})

    async def test_only_callback_route_is_exposed(self):
        app, tinyiot = self.build()
        response = await self.request(app, '/api/health', ASSOC)
        self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.request(app, '/docs')).status_code, 404)
        self.assertEqual((await self.request(app, '/openapi.json')).status_code, 404)
        response = await self.request(app, '/api/mec/notifications/' + TOKEN, ASSOC)
        self.assertEqual(response.status_code, 200)
        tinyiot.create_named_verified_content.assert_awaited_once()

    async def test_disabled_listener_rejects(self):
        app, tinyiot = self.build(False)
        response = await self.request(app, '/api/mec/notifications/' + TOKEN, ASSOC)
        self.assertEqual(response.status_code, 404)
        tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_recovery_runs_at_startup_and_failure_fails_closed(self):
        recovery = AsyncMock(return_value={'restored': 1, 'rejected': 0})
        app, _ = self.build(recovery=recovery)
        async with app.router.lifespan_context(app):
            self.assertEqual(app.state.mec_recovery['restored'], 1)
        recovery.assert_awaited_once()

        failed = AsyncMock(side_effect=RuntimeError('remote unavailable'))
        app, tinyiot = self.build(recovery=failed)
        # The existing in-memory test binding is still present, but a real
        # restart constructs an empty registry before recovery is attempted.
        fresh_receiver = MecNotificationReceiver(
            tinyiot, MecNotificationConfig(enabled=True),
            bindings=MecSubscriptionBindingRegistry())
        app = create_callback_app(fresh_receiver, recovery=failed)
        async with app.router.lifespan_context(app):
            response = await self.request(app, '/api/mec/notifications/' + TOKEN, ASSOC)
            self.assertEqual(response.status_code, 401)

    def test_persistent_store_does_not_contain_callback_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            store = MecBindingStateStore(path, 'z' * 32)
            store.upsert(SUBSCRIPTION, TOKEN,
                         'https://callback.test/api/mec/notifications/' + TOKEN, None)
            self.assertNotIn(TOKEN, path.read_text(encoding='utf-8'))

    async def test_creator_and_callback_listener_share_pending_active_and_revoke_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = Path(directory) / 'state.json'
            key = 'cross-process-key-' + ('x' * 32)
            creator_store = MecBindingStateStore(store_path, key)
            listener_store = MecBindingStateStore(store_path, key)
            creator = MecNotificationReceiver(
                AsyncMock(), MecNotificationConfig(True, bridge_origin='CMECBridge'),
                bindings=MecSubscriptionBindingRegistry(state_store=creator_store))
            listener_tinyiot = AsyncMock()
            listener_tinyiot.create_named_verified_content.return_value = True
            listener = MecNotificationReceiver(
                listener_tinyiot, MecNotificationConfig(True, bridge_origin='CMECBridge'),
                bindings=MecSubscriptionBindingRegistry(state_store=listener_store))
            mec = AsyncMock()
            remote = {
                'subscriptionId': SUBSCRIPTION,
                'state': 'ACTIVE',
            }
            lifecycle = MecSubscriptionLifecycle(
                mec, creator, 'https://callback.test',
                token_factory=lambda _: TOKEN)
            pending = lifecycle.prepare_assoc_sta('005C07070707')
            callback_app = create_callback_app(listener)
            pending_response = await self.request(
                callback_app, '/api/mec/notifications/' + TOKEN,
                {'notificationType': 'TestNotification', '_links': {}})
            self.assertEqual(pending_response.status_code, 200)
            self.assertEqual(pending_response.json()['code'], 'pending_test_notification_validated')

            remote['callbackReference'] = pending.callback_reference
            mec.create_assoc_sta_subscription.return_value = remote
            await lifecycle.create_assoc_sta(pending)

            async def reconcile():
                listener.bindings.clear_active()
                listener.bindings.sync_pending()
                for record in listener.bindings.persisted_bindings():
                    if record.status == 'active':
                        listener.bindings.restore_verified(
                            SUBSCRIPTION, TOKEN, pending.callback_reference)
                return {'restored': 1, 'rejected': 0}

            callback_app = create_callback_app(listener, recovery=reconcile)
            event_response = await self.request(
                callback_app, '/api/mec/notifications/' + TOKEN, ASSOC)
            self.assertEqual(event_response.status_code, 200)
            self.assertTrue(event_response.json()['published'])

            creator.revoke_subscription(SUBSCRIPTION)
            revoked_response = await self.request(
                callback_app, '/api/mec/notifications/' + TOKEN, ASSOC)
            self.assertEqual(revoked_response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
