import unittest

import httpx

from backend.mec_registry import (MecRegistryError, MecRegistryErrorCode,
                                  MecServiceDiscoveryConfig, MecServiceRegistry)


BASE = 'https://mec.test/session/mep1/mec_service_mgmt/v1'
WLAN = 'https://mec.test/session/mep1/wai/v2/'
SERVICES = [
    {'serInstanceId': 'wai-instance', 'serName': 'mep1-mec028-1',
     'serCategory': {'id': 'waiId', 'name': 'WAI'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': [WLAN]}}},
    {'serInstanceId': 'loc-instance', 'serName': 'mep1-mec013-1',
     'serCategory': {'id': 'locationId', 'name': 'Location'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': ['https://mec.test/session/mep1/location/v2/']}}},
    {'serInstanceId': 'rni-instance', 'serName': 'mep1-mec012-1',
     'serCategory': {'id': 'rniId', 'name': 'RNI'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': ['https://mec.test/session/mep1/rni/v2/']}}},
    {'serInstanceId': 'bw-instance', 'serName': 'mep1-mec015-bwm-1',
     'serCategory': {'id': 'bwmId', 'name': 'BWM'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': ['https://mec.test/session/mep1/bwm/v1/']}}},
    {'serInstanceId': 'mts-instance', 'serName': 'mep1-mec015-mts-1',
     'serCategory': {'id': 'mtsId', 'name': 'MTS'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': ['https://mec.test/session/mep1/mts/v1/']}}},
    {'serInstanceId': 'device-instance', 'serName': 'mep1-mec016-1',
     'serCategory': {'id': 'deviceId', 'name': 'DAI'}, 'state': 'ACTIVE',
     'transportInfo': {'endpoint': {'uris': ['https://mec.test/session/mep1/device/v1/']}}},
]


class MecRegistryTests(unittest.IsolatedAsyncioTestCase):
    def make_registry(self, payload=SERVICES, *, clock=None, ttl=30,
                      fallback='', status=200, exception=None):
        calls = []

        async def handler(request):
            calls.append(request)
            if exception:
                raise exception
            return httpx.Response(status, headers={'content-type': 'application/json'},
                                  json=payload if status == 200 else {})

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        registry = MecServiceRegistry(
            MecServiceDiscoveryConfig(True, BASE, fallback, ttl), upstream, clock=clock or (lambda: 0.0))
        return registry, upstream, calls

    async def test_valid_six_service_response_and_wlan_selection(self):
        registry, upstream, calls = self.make_registry()
        try:
            services = await registry.discover_services()
            selected = await registry.select_wlan()
            self.assertEqual(len(services), 6)
            self.assertEqual(selected.service.instance_id, 'wai-instance')
            self.assertEqual(selected.service.category_id, 'waiId')
            self.assertEqual(selected.endpoint, WLAN)
            self.assertTrue(selected.dynamic_discovery)
            self.assertEqual(len(calls), 1)
        finally:
            await upstream.aclose()
    async def test_preserves_service_ids_and_category_metadata(self):
        registry, upstream, _ = self.make_registry()
        try:
            service = (await registry.discover_services())[0]
            self.assertEqual(service.as_dict(), {
                'instance_id': 'wai-instance', 'name': 'mep1-mec028-1',
                'category_id': 'waiId', 'category_name': 'WAI', 'state': 'ACTIVE',
                'endpoint_uris': [WLAN]})
        finally:
            await upstream.aclose()
    async def test_inactive_and_missing_wlan_service(self):
        for payload, code in (([{**SERVICES[0], 'state': 'INACTIVE'}], MecRegistryErrorCode.SERVICE_INACTIVE),
                              (SERVICES[1:], MecRegistryErrorCode.SERVICE_NOT_FOUND)):
            registry, upstream, _ = self.make_registry(payload)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.select_wlan()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_multiple_matching_services_is_ambiguous(self):
        duplicate = {**SERVICES[0], 'serInstanceId': 'wai-2'}
        registry, upstream, _ = self.make_registry(SERVICES + [duplicate])
        try:
            with self.assertRaises(MecRegistryError) as caught:
                await registry.select_wlan()
            self.assertEqual(caught.exception.code, MecRegistryErrorCode.AMBIGUOUS_SERVICE)
        finally:
            await upstream.aclose()

    async def test_empty_and_malformed_responses(self):
        for payload in ({}, [{'serInstanceId': 'broken'}]):
            registry, upstream, _ = self.make_registry(payload)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.discover_services()
                self.assertEqual(caught.exception.code, MecRegistryErrorCode.INVALID_RESPONSE)
            finally:
                await upstream.aclose()
        registry, upstream, _ = self.make_registry([])
        try:
            with self.assertRaises(MecRegistryError) as caught:
                await registry.select_wlan()
            self.assertEqual(caught.exception.code, MecRegistryErrorCode.SERVICE_NOT_FOUND)
        finally:
            await upstream.aclose()

    async def test_malformed_json_and_unexpected_content_type(self):
        async def malformed(request):
            return httpx.Response(200, headers={'content-type': 'application/json'}, content=b'{')
        async def html(request):
            return httpx.Response(200, headers={'content-type': 'text/html'}, content=b'[]')
        for handler, code in ((malformed, MecRegistryErrorCode.INVALID_JSON),
                              (html, MecRegistryErrorCode.INVALID_CONTENT_TYPE)):
            upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            registry = MecServiceRegistry(MecServiceDiscoveryConfig(True, BASE), upstream)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.discover_services()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_invalid_and_external_endpoints(self):
        for endpoint, code in (('http://mec.test/wai', MecRegistryErrorCode.INVALID_ENDPOINT),
                               ('https://external.test/wai', MecRegistryErrorCode.UNTRUSTED_ENDPOINT),
                               ('https://mec.test/wai?token=secret', MecRegistryErrorCode.INVALID_ENDPOINT)):
            payload = [{**SERVICES[0], 'transportInfo': {'endpoint': {'uris': [endpoint]}}}]
            registry, upstream, _ = self.make_registry(payload)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.select_wlan()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_http_errors_timeout_and_connection_errors(self):
        for status in (401, 403, 404, 500):
            registry, upstream, _ = self.make_registry(status=status)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.discover_services()
                self.assertEqual(caught.exception.code, MecRegistryErrorCode.HTTP_ERROR)
                self.assertEqual(caught.exception.status_code, status)
            finally:
                await upstream.aclose()
        for exception, code in ((httpx.ReadTimeout('slow'), MecRegistryErrorCode.TIMEOUT),
                                (httpx.ConnectError('offline'), MecRegistryErrorCode.CONNECTION_ERROR)):
            registry, upstream, _ = self.make_registry(exception=exception)
            try:
                with self.assertRaises(MecRegistryError) as caught:
                    await registry.discover_services()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_cache_reuse_expiry_and_refresh(self):
        now = [0.0]
        registry, upstream, calls = self.make_registry(clock=lambda: now[0], ttl=10)
        try:
            await registry.discover_services()
            await registry.discover_services()
            self.assertEqual(len(calls), 1)
            now[0] = 11.0
            await registry.discover_services()
            self.assertEqual(len(calls), 2)
            await registry.refresh()
            self.assertEqual(len(calls), 3)
            self.assertTrue(registry.cache_status()['cached'])
        finally:
            await upstream.aclose()

    async def test_configured_fallback_is_explicitly_labeled(self):
        registry, upstream, _ = self.make_registry(SERVICES[1:], fallback=WLAN)
        try:
            selected = await registry.select_wlan()
            self.assertEqual(selected.endpoint, WLAN)
            self.assertFalse(selected.dynamic_discovery)
            self.assertEqual(selected.diagnostics.source, 'configured_fallback')
            self.assertTrue(selected.diagnostics.fallback_used)
        finally:
            await upstream.aclose()

    async def test_services_url_appends_services(self):
        registry, upstream, calls = self.make_registry()
        try:
            await registry.discover_services()
            self.assertEqual(str(calls[0].url), BASE + '/services')
        finally:
            await upstream.aclose()
