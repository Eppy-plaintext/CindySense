import unittest

import httpx

from backend.mec_client import MecClientError, MecErrorCode, MecWlanClient, MecWlanConfig


VALID = [{'apId': {'macId': '00:11:22:33:44:55'}, 'staCount': 1}]


class MecClientTests(unittest.IsolatedAsyncioTestCase):
    async def request_client(self, response=None, exception=None, *, config=None):
        async def handler(request):
            if exception:
                raise exception
            return response
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = MecWlanClient(config or MecWlanConfig(True, 'https://mec.test/wai/v2/queries/ap/ap_information'), upstream)
        return client, upstream

    async def test_valid_response(self):
        client, upstream = await self.request_client(
            httpx.Response(200, headers={'content-type': 'application/json'}, json=VALID))
        try:
            self.assertEqual(await client.fetch_access_points(), VALID)
        finally:
            await upstream.aclose()

    async def test_base_url_targets_mec_ap_information_resource(self):
        requested_urls = []

        async def handler(request):
            requested_urls.append(str(request.url))
            return httpx.Response(200, headers={'content-type': 'application/json'}, json=VALID)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = MecWlanClient(MecWlanConfig(True, 'https://mec.test/mep/wai/v2'), upstream)
        try:
            await client.fetch_access_points()
            self.assertEqual(requested_urls, ['https://mec.test/mep/wai/v2/queries/ap/ap_information'])
        finally:
            await upstream.aclose()

    async def test_full_ap_information_url_is_not_duplicated(self):
        requested_urls = []

        async def handler(request):
            requested_urls.append(str(request.url))
            return httpx.Response(200, headers={'content-type': 'application/json'}, json=VALID)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        full_url = 'https://mec.test/mep/wai/v2/queries/ap/ap_information'
        client = MecWlanClient(MecWlanConfig(True, full_url), upstream)
        try:
            await client.fetch_access_points()
            self.assertEqual(requested_urls, [full_url])
        finally:
            await upstream.aclose()

    async def test_empty_list_is_valid(self):
        client, upstream = await self.request_client(
            httpx.Response(200, headers={'content-type': 'application/json'}, json=[]))
        try:
            self.assertEqual(await client.fetch_access_points(), [])
        finally:
            await upstream.aclose()

    async def test_http_errors(self):
        for status in (401, 403, 404, 500):
            client, upstream = await self.request_client(
                httpx.Response(status, headers={'content-type': 'application/json'}, json={}))
            try:
                with self.assertRaises(MecClientError) as caught:
                    await client.fetch_access_points()
                self.assertEqual(caught.exception.code, MecErrorCode.HTTP_ERROR)
                self.assertEqual(caught.exception.status_code, status)
            finally:
                await upstream.aclose()

    async def test_timeout_and_connection_errors(self):
        for exc, code in ((httpx.ReadTimeout('slow'), MecErrorCode.TIMEOUT),
                          (httpx.ConnectError('offline'), MecErrorCode.CONNECTION_ERROR)):
            client, upstream = await self.request_client(exception=exc)
            try:
                with self.assertRaises(MecClientError) as caught:
                    await client.fetch_access_points()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_malformed_json_and_content_type(self):
        for response, code in (
            (httpx.Response(200, headers={'content-type': 'application/json'}, content=b'{'), MecErrorCode.INVALID_JSON),
            (httpx.Response(200, headers={'content-type': 'text/html'}, content=b'[]'), MecErrorCode.INVALID_CONTENT_TYPE),
            (httpx.Response(200, headers={'content-type': 'application/json'}, json={'items': []}), MecErrorCode.INVALID_RESPONSE),
        ):
            client, upstream = await self.request_client(response)
            try:
                with self.assertRaises(MecClientError) as caught:
                    await client.fetch_access_points()
                self.assertEqual(caught.exception.code, code)
            finally:
                await upstream.aclose()

    async def test_configuration_validation_and_tls_defaults(self):
        with self.assertRaises(MecClientError):
            MecWlanClient(MecWlanConfig(True, 'http://mec.test/api'))
        with self.assertRaises(MecClientError):
            MecWlanClient(MecWlanConfig(True, 'https://mec.test/api?x=1'))
        with self.assertRaises(MecClientError):
            MecWlanClient(MecWlanConfig(True, 'https://mec.test/api', 0))
        disabled = MecWlanClient(MecWlanConfig(False, ''))
        with self.assertRaises(MecClientError) as caught:
            await disabled.fetch_access_points()
        self.assertEqual(caught.exception.code, MecErrorCode.DISABLED)
