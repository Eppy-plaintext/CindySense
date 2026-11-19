import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.mec_bridge_runner import build_bridge, synchronize_once


def config(**overrides):
    values = {
        'base_url': 'http://fixture/TinyIoT',
        'mec_bridge_enabled': True,
        'mec_bridge_origin': 'CMECBridge',
        'mec_bridge_ae': 'MECBridge_AE',
        'mec_bridge_wlan_container': 'WLANInformation',
        'mec_service_discovery_enabled': True,
        'mec_allow_configured_fallback': False,
        'mec_enabled': True,
        'mec_wlan_api_base_url': 'https://mec.test/mep/wai/v2',
        'mec_request_timeout_seconds': 3,
        'mec_service_mgmt_api_base_url': 'https://mec.test/mep1/mec_service_mgmt/v1',
        'mec_service_discovery_ttl_seconds': 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MecBridgeRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_existing_bridge_with_dynamic_discovery_and_dedicated_origin(self):
        bridge = build_bridge(config())
        try:
            self.assertTrue(bridge.config.enabled)
            self.assertTrue(bridge.config.dynamic_service_discovery)
            self.assertEqual(bridge.config.origin, 'CMECBridge')
            self.assertIsNotNone(bridge.registry)
            self.assertEqual(bridge.wlan_resource_path, 'MECBridge_AE/WLANInformation')
        finally:
            await bridge.tinyiot._client.aclose()

    async def test_disabled_bridge_remains_disabled_without_a_valid_mec_endpoint(self):
        bridge = build_bridge(config(mec_bridge_enabled=False, mec_enabled=False,
                                    mec_wlan_api_base_url='', mec_service_discovery_enabled=False))
        try:
            self.assertFalse(bridge.config.enabled)
            self.assertIsNone(bridge.registry)
        finally:
            await bridge.tinyiot._client.aclose()

    def test_enabled_bridge_requires_its_existing_dedicated_origin(self):
        with patch('backend.mec_bridge_runner.httpx.AsyncClient') as client:
            with self.assertRaises(ValueError):
                build_bridge(config(mec_bridge_origin=''))
            client.assert_not_called()

    async def test_explicit_runner_leaves_dependencies_untouched_when_bridge_is_disabled(self):
        result = await synchronize_once(config=config(
            mec_bridge_enabled=False, mec_enabled=False,
            mec_wlan_api_base_url='', mec_service_discovery_enabled=False))
        self.assertFalse(result.success)
        self.assertEqual(result.code, 'bridge_disabled')
