import asyncio
import unittest

from backend.discovery import (AE_TYPE, CONTAINER_TYPE, SUBSCRIPTION_TYPE,
                               DiscoveryStatus, ResourceResolver)
from backend.tinyiot import TinyIoTError


class FakeClient:
    base_url = "http://mock/TinyIoT"

    def __init__(self):
        self.discovery = {}
        self.retrieves = {}
        self.discover_calls = []

    async def discover(self, path, origin, resource_type=None, label=None):
        self.discover_calls.append((path, origin, resource_type, label))
        value = self.discovery.get((path, resource_type))
        if isinstance(value, Exception):
            raise value
        return list(value or [])

    async def retrieve(self, path, origin, expected_not_found=False):
        value = self.retrieves.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise TinyIoTError("resource_not_found", "missing", 404)
        return value


def error(code, status):
    return TinyIoTError(code, code, status)


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.resolver = ResourceResolver(self.client, "discover-origin", ttl_seconds=10)

    async def test_ae_discovery_normalizes_tinyiot_relative_paths(self):
        self.client.discovery[("", AE_TYPE)] = ["TinyIoT/CindySense_AE", "/TinyIoT/CindyPT_AE"]
        result = await self.resolver.discover_aes()
        self.assertEqual(result.resources, ("CindySense_AE", "CindyPT_AE"))
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.DISCOVERED)

    async def test_container_discovery_and_subscription_discovery(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = [
            "TinyIoT/CindySense_AE/RepetitionCount"]
        self.client.discovery[("CindySense_AE/RepetitionCount", SUBSCRIPTION_TYPE)] = [
            "TinyIoT/CindySense_AE/RepetitionCount/RepCount_Sub"]
        containers = await self.resolver.discover_containers("CindySense_AE")
        subscriptions = await self.resolver.discover_subscriptions("CindySense_AE", "RepetitionCount")
        self.assertEqual(containers.resources, ("CindySense_AE/RepetitionCount",))
        self.assertEqual(subscriptions.resources, ("CindySense_AE/RepetitionCount/RepCount_Sub",))

    async def test_rejects_malformed_external_and_traversal_uris(self):
        for value in ("", "../CindySense_AE/X", "TinyIoT/../X", "http://evil/X",
                      "http://mock/Other/X", "TinyIoT/CindySense_AE/X?x=1"):
            self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = [value]
            result = await self.resolver.discover_containers("CindySense_AE", refresh=True)
            self.assertEqual(result.diagnostics.status, DiscoveryStatus.INVALID_RESPONSE, value)

    async def test_exact_semantic_selection_rejects_wrong_scope_and_type(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = [
            "TinyIoT/CindySense_AE/RepetitionCount",
            "TinyIoT/CindySense_AE/WorkoutSummary"]
        result = await self.resolver.resolve(ae_scope="CindySense_AE", name="RepetitionCount",
                                             resource_type=CONTAINER_TYPE, operation_origin="workout")
        self.assertEqual(result.path, "CindySense_AE/RepetitionCount")
        self.assertTrue(result.diagnostics.verified)
        missing = await self.resolver.resolve(ae_scope="CindyPT_AE", name="RepetitionCount",
                                              resource_type=CONTAINER_TYPE, operation_origin="pt")
        self.assertEqual(missing.diagnostics.status, DiscoveryStatus.MISSING)

    async def test_missing_resource_is_structured(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = []
        result = await self.resolver.resolve(ae_scope="CindySense_AE", name="WorkoutSummary",
                                             resource_type=CONTAINER_TYPE, operation_origin="workout")
        self.assertIsNone(result.path)
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.MISSING)

    async def test_forbidden_discovery_is_distinguished(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = error("tinyiot_forbidden", 403)
        result = await self.resolver.discover_containers("CindySense_AE")
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.FORBIDDEN)
        self.assertEqual(result.diagnostics.error_code, "tinyiot_forbidden")

    async def test_fallback_is_validated_with_operation_origin(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = error("tinyiot_forbidden", 403)
        self.client.retrieves["CindySense_AE/WorkoutSummary"] = {"m2m:cnt": {"rn": "WorkoutSummary"}}
        result = await self.resolver.resolve(ae_scope="CindySense_AE", name="WorkoutSummary",
                                             resource_type=CONTAINER_TYPE, operation_origin="workout",
                                             fallback_path="TinyIoT/CindySense_AE/WorkoutSummary")
        self.assertEqual(result.path, "CindySense_AE/WorkoutSummary")
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.FALLBACK)
        self.assertTrue(result.diagnostics.verified)

    async def test_unpermitted_or_invalid_fallback_is_not_used(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = error("tinyiot_unreachable", 503)
        self.client.retrieves["CindySense_AE/WorkoutSummary"] = error("tinyiot_forbidden", 403)
        result = await self.resolver.resolve(ae_scope="CindySense_AE", name="WorkoutSummary",
                                             resource_type=CONTAINER_TYPE, operation_origin="workout",
                                             fallback_path="TinyIoT/CindySense_AE/WorkoutSummary")
        self.assertIsNone(result.path)
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.FORBIDDEN)
        result = await self.resolver.resolve(ae_scope="CindySense_AE", name="WorkoutSummary",
                                             resource_type=CONTAINER_TYPE, operation_origin="workout",
                                             fallback_path="http://evil/CindySense_AE/WorkoutSummary")
        self.assertIsNone(result.path)

    async def test_ttl_cache_expiry_and_explicit_invalidation(self):
        now = [100.0]
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = ["TinyIoT/CindySense_AE/RepetitionCount"]
        resolver = ResourceResolver(self.client, "discover-origin", ttl_seconds=5, clock=lambda: now[0])
        first = await resolver.discover_containers("CindySense_AE")
        second = await resolver.discover_containers("CindySense_AE")
        self.assertFalse(first.diagnostics.from_cache)
        self.assertTrue(second.diagnostics.from_cache)
        self.assertEqual(len(self.client.discover_calls), 1)
        now[0] = 106
        await resolver.discover_containers("CindySense_AE")
        self.assertEqual(len(self.client.discover_calls), 2)
        resolver.invalidate(ae_scope="CindySense_AE", resource_type=CONTAINER_TYPE)
        await resolver.discover_containers("CindySense_AE")
        self.assertEqual(len(self.client.discover_calls), 3)

    async def test_refresh_bypasses_cache(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = []
        await self.resolver.discover_containers("CindySense_AE")
        await self.resolver.refresh(ae_scope="CindySense_AE", resource_type=CONTAINER_TYPE)
        self.assertEqual(len(self.client.discover_calls), 2)

    async def test_duplicate_resources_are_deduplicated_and_ambiguous_names_do_not_guess(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = [
            "TinyIoT/CindySense_AE/RepetitionCount",
            "TinyIoT/CindySense_AE/RepetitionCount"]
        result = await self.resolver.discover_containers("CindySense_AE")
        self.assertEqual(result.resources, ("CindySense_AE/RepetitionCount",))
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = [
            "TinyIoT/CindySense_AE/RepetitionCount",
            "TinyIoT/CindySense_AE/RepetitionCount"]
        resolved = await self.resolver.resolve(ae_scope="CindySense_AE", name="RepetitionCount",
                                                resource_type=CONTAINER_TYPE, operation_origin="workout",
                                                refresh=True)
        self.assertEqual(resolved.path, "CindySense_AE/RepetitionCount")

    async def test_unexpected_tinyiot_response_is_not_cached(self):
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = error("invalid_tinyiot_response", 502)
        result = await self.resolver.discover_containers("CindySense_AE")
        self.assertEqual(result.diagnostics.status, DiscoveryStatus.INVALID_RESPONSE)
        self.client.discovery[("CindySense_AE", CONTAINER_TYPE)] = ["TinyIoT/CindySense_AE/WorkoutSummary"]
        result = await self.resolver.discover_containers("CindySense_AE")
        self.assertEqual(result.resources, ("CindySense_AE/WorkoutSummary",))


if __name__ == "__main__":
    unittest.main()
