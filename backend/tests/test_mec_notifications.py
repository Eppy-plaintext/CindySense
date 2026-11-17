import unittest
import asyncio
from unittest.mock import AsyncMock

from backend.mec_notifications import (MecNotificationConfig, MecNotificationReceiver,
                                        MecSubscriptionBindingRegistry)


ASSOC = {'notificationType': 'AssocStaNotification',
         'timeStamp': '2026-11-17T00:00:00Z',
         'apId': {'bssid': '005C07070707'},
         'staId': {'macAddress': '001122334455'}}
TEST = {'notificationType': 'TestNotification', 'timeStamp': '2026-11-17T00:00:00Z'}


class MecNotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tinyiot = AsyncMock()
        self.tinyiot.create_named_verified_content.return_value = True
        self.receiver = MecNotificationReceiver(
            self.tinyiot, MecNotificationConfig(True, 'secret', allowed_subscription_ids=('sub-1',)),
            bindings=MecSubscriptionBindingRegistry())
        self.receiver.register_subscription('sub-1', 'secret')

    async def test_valid_association_is_published_with_source_and_correlation(self):
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1',
                                             headers={'X-Correlation-ID': 'corr-1'})
        self.assertTrue(result['published']); self.assertTrue(result['verified'])
        args = self.tinyiot.create_named_verified_content.await_args.args
        self.assertEqual(args[0], 'MECBridge_AE/WLANNotifications')
        self.assertEqual(args[1], '')
        self.assertEqual(args[3]['source'], 'ETSI MEC Sandbox simulated network data')
        self.assertEqual(args[3]['correlationId'], 'corr-1')

    async def test_test_notification_is_validated_without_publication(self):
        result = await self.receiver.receive({**TEST, '_links': {}}, token='secret', subscription_id='sub-1')
        self.assertEqual(result['code'], 'test_notification_validated')
        self.tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_bad_auth_does_not_publish(self):
        result = await self.receiver.receive(ASSOC, token='wrong', subscription_id='sub-1')
        self.assertEqual(result['code'], 'invalid_callback_credentials')
        self.tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_missing_header_does_not_block_registered_callback(self):
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id=None)
        self.assertTrue(result['accepted'])

    async def test_revoked_subscription_is_rejected(self):
        self.receiver.revoke_subscription('sub-1')
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id=None)
        self.assertEqual(result['code'], 'invalid_callback_credentials')

    async def test_missing_subscription_and_unknown_event_are_rejected(self):
        missing = await self.receiver.receive(ASSOC, token='secret', subscription_id=None)
        self.assertTrue(missing['accepted'])
        unknown = await self.receiver.receive({'notificationType': 'Other'}, token='secret', subscription_id='sub-1')
        self.assertEqual(unknown['code'], 'malformed_or_unsupported_payload')

    async def test_duplicate_is_not_published_twice(self):
        first = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1')
        second = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1')
        self.assertTrue(first['published']); self.assertTrue(second['duplicate'])
        self.tinyiot.create_named_verified_content.assert_awaited_once()

    async def test_oversized_and_disabled_requests_are_rejected(self):
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1', body_size=70000)
        self.assertEqual(result['code'], 'body_too_large')
        disabled = MecNotificationReceiver(self.tinyiot, MecNotificationConfig(False, 'secret'))
        result = await disabled.receive(ASSOC, token='secret', subscription_id='sub-1')
        self.assertEqual(result['code'], 'notifications_disabled')

    async def test_publication_and_readback_failures_are_not_success(self):
        self.tinyiot.create_named_verified_content.return_value = False
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1')
        self.assertEqual(result['code'], 'readback_failed')
        self.tinyiot.create_named_verified_content.side_effect = RuntimeError('offline')
        result = await self.receiver.receive({**ASSOC, 'timeStamp': 'different'}, token='secret', subscription_id='sub-1')
        self.assertEqual(result['code'], 'publication_failed')

    async def test_publication_timeout_is_structured(self):
        self.tinyiot.create_named_verified_content.side_effect = asyncio.TimeoutError()
        result = await self.receiver.receive(ASSOC, token='secret', subscription_id='sub-1')
        self.assertEqual(result['code'], 'publication_timeout')


if __name__ == '__main__':
    unittest.main()
