import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend import main


TOKEN = 'mec-callback-secret'
SUBSCRIPTION = 'mec-sub-1'
ASSOC = {
    'notificationType': 'AssocStaNotification',
    'timeStamp': '2026-11-17T00:00:00Z',
    'apId': {'bssid': '005C07070707'},
    'staId': {'macAddress': '001122334455'},
}


class MecCallbackApiTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, payload, *, settings, tinyiot, token=TOKEN,
                      subscription=SUBSCRIPTION, headers=None):
        request_headers = {'content-type': 'application/json',
                           'X-MEC-Subscription-ID': subscription}
        request_headers.update(headers or {})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url='http://testserver') as client:
            with patch.object(main, 'settings', settings), patch.object(main, 'tinyiot', tinyiot):
                if getattr(settings, 'mec_notifications_enabled', False) and token == TOKEN:
                    main.mec_notification_receiver.register_subscription(SUBSCRIPTION, token)
                return await client.post('/api/mec/notifications/' + token,
                                         content=payload if isinstance(payload, bytes) else None,
                                         json=None if isinstance(payload, bytes) else payload,
                                         headers=request_headers)

    def enabled_settings(self):
        return SimpleNamespace(mec_notifications_enabled=True,
                               mec_callback_token=TOKEN,
                               mec_bridge_origin='CMECBridge',
                               mec_allowed_subscription_ids=(SUBSCRIPTION,))

    async def test_disabled_by_default_rejects_without_publication(self):
        tinyiot = AsyncMock()
        response = await self.request(ASSOC, settings=SimpleNamespace(), tinyiot=tinyiot)
        self.assertEqual(response.status_code, 404)
        tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_invalid_token_is_rejected(self):
        response = await self.request(ASSOC, settings=self.enabled_settings(),
                                      tinyiot=AsyncMock(), token='wrong')
        self.assertEqual(response.status_code, 401)

    async def test_valid_association_is_published_and_verified(self):
        tinyiot = AsyncMock(); tinyiot.create_named_verified_content.return_value = True
        response = await self.request(ASSOC, settings=self.enabled_settings(), tinyiot=tinyiot,
                                      headers={'X-Correlation-ID': 'corr-api'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['published'])
        self.assertEqual(tinyiot.create_named_verified_content.await_args.args[3]['correlationId'], 'corr-api')

    async def test_test_notification_does_not_publish(self):
        tinyiot = AsyncMock()
        response = await self.request({'notificationType': 'TestNotification', '_links': {}},
                                      settings=self.enabled_settings(), tinyiot=tinyiot)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['code'], 'test_notification_validated')
        tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_oversized_and_malformed_requests_are_rejected(self):
        settings = self.enabled_settings()
        oversized = await self.request(b'x' * (64 * 1024 + 1), settings=settings,
                                       tinyiot=AsyncMock())
        self.assertEqual(oversized.status_code, 413)
        malformed = await self.request(b'{bad', settings=settings, tinyiot=AsyncMock())
        self.assertEqual(malformed.status_code, 400)

    async def test_unknown_event_and_subscription_are_rejected(self):
        settings = self.enabled_settings()
        unknown_event = await self.request({'notificationType': 'Unknown'}, settings=settings,
                                           tinyiot=AsyncMock())
        self.assertEqual(unknown_event.status_code, 400)
        unknown_sub = await self.request(ASSOC, settings=settings, tinyiot=AsyncMock(),
                                         subscription='not-allowlisted')
        self.assertEqual(unknown_sub.status_code, 403)

    async def test_duplicate_does_not_publish_twice(self):
        tinyiot = AsyncMock(); tinyiot.create_named_verified_content.return_value = True
        first = await self.request({**ASSOC, 'timeStamp': 'duplicate-1'},
                                   settings=self.enabled_settings(), tinyiot=tinyiot)
        second = await self.request({**ASSOC, 'timeStamp': 'duplicate-1'},
                                    settings=self.enabled_settings(), tinyiot=tinyiot)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()['code'], 'duplicate')
        tinyiot.create_named_verified_content.assert_awaited_once()

    async def test_tinyiot_failure_is_not_success(self):
        tinyiot = AsyncMock()
        tinyiot.create_named_verified_content.side_effect = RuntimeError('offline')
        response = await self.request({**ASSOC, 'timeStamp': 'failure-1'},
                                      settings=self.enabled_settings(), tinyiot=tinyiot)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['accepted'])


if __name__ == '__main__':
    unittest.main()
