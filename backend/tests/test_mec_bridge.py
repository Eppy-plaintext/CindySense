import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from backend.mec_bridge import MecBridge, MecBridgeConfig
from backend.mec_client import MecClientError, MecErrorCode
from backend.mec_mapping import MecWlanMappingError, SIMULATED_SOURCE
from backend.tinyiot import TinyIoTError


PAYLOAD = [{'apId': {'bssid': '005C07070707'},
            'bssLoad': {'staCount': 0, 'channelUtilization': 0, 'availAdmCap': 0}}]


class MecBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mec = AsyncMock()
        self.tinyiot = AsyncMock()
        self.config = MecBridgeConfig(True, 'bridge-origin')
        self.bridge = MecBridge(self.mec, self.tinyiot, self.config,
                                 clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.mec.fetch_access_points.return_value = PAYLOAD
        self.tinyiot.create_named_verified_content.return_value = True

    async def test_success_payload_integrity_source_and_correlation(self):
        result = await self.bridge.synchronize_once('request-123')
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        args = self.tinyiot.create_named_verified_content.await_args.args
        self.assertEqual(args[:2], ('MECBridge_AE/WLANInformation', 'bridge-origin'))
        self.assertEqual(args[2], MecBridge.content_instance_name('request-123'))
        content = args[3]
        self.assertEqual(content['requestId'], 'request-123')
        self.assertEqual(content['source'], SIMULATED_SOURCE)
        self.assertEqual(content['accessPoints'][0]['stationCount'], 0)
        self.assertEqual(content['accessPoints'][0]['channelUtilization'], 0)
        self.assertEqual(content['accessPoints'][0]['availableAdmissionCapacity'], 0)

    async def test_disabled_bridge_does_not_call_dependencies(self):
        bridge = MecBridge(self.mec, self.tinyiot, MecBridgeConfig(False))
        result = await bridge.synchronize_once('disabled')
        self.assertFalse(result.success)
        self.mec.fetch_access_points.assert_not_awaited()
        self.tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_invalid_mec_response(self):
        self.mec.fetch_access_points.side_effect = MecWlanMappingError('invalid', index=0)
        result = await self.bridge.synchronize_once('bad-response')
        self.assertFalse(result.success)
        self.assertEqual(result.code, 'invalid_wlan_access_point')
        self.tinyiot.create_named_verified_content.assert_not_awaited()

    async def test_mec_timeout(self):
        self.mec.fetch_access_points.side_effect = MecClientError(MecErrorCode.TIMEOUT, 'timeout', 504)
        result = await self.bridge.synchronize_once('timeout')
        self.assertFalse(result.success)
        self.assertEqual(result.code, 'mec_timeout')

    async def test_missing_resource_forbidden_timeout_and_publish_failures(self):
        for error in (TinyIoTError('resource_not_found', 'missing', 404),
                      TinyIoTError('tinyiot_forbidden', 'forbidden', 403),
                      TinyIoTError('tinyiot_unreachable', 'offline', 503),
                      TinyIoTError('tinyiot_error', 'failed', 502)):
            self.tinyiot.create_named_verified_content.reset_mock()
            self.tinyiot.create_named_verified_content.side_effect = error
            result = await self.bridge.synchronize_once('write-failure')
            self.assertFalse(result.success)
            self.assertFalse(result.verified)
            self.assertTrue(result.code.startswith('onem2m_'))

    async def test_readback_failure_is_not_success(self):
        self.tinyiot.create_named_verified_content.side_effect = TinyIoTError(
            'content_unconfirmed', 'readback failed', 502)
        result = await self.bridge.synchronize_once('readback-failure')
        self.assertFalse(result.success)
        self.assertFalse(result.verified)

    async def test_same_request_id_is_retry_safe(self):
        first = await self.bridge.synchronize_once('same-request')
        self.tinyiot.create_named_verified_content.reset_mock()
        self.tinyiot.create_named_verified_content.return_value = False
        second = await self.bridge.synchronize_once('same-request')
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(first.content_instance_name, second.content_instance_name)

    async def test_mec_and_one_m2m_calls_are_isolated(self):
        self.mec.fetch_access_points.side_effect = MecClientError(
            MecErrorCode.CONNECTION_ERROR, 'offline', 503)
        result = await self.bridge.synchronize_once('isolated')
        self.assertFalse(result.success)
        self.tinyiot.create_named_verified_content.assert_not_awaited()

    def test_configuration_requires_origin_when_enabled(self):
        with self.assertRaises(ValueError):
            MecBridge(self.mec, self.tinyiot, MecBridgeConfig(True, ''))
