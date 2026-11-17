import asyncio
import unittest
from types import SimpleNamespace

from backend.subscription_monitor import SubscriptionMonitor
from backend.tinyiot import TinyIoTError


TOKEN = "t" * 40
URL = "http://172.20.0.1:8000/api/onem2m/notify/" + TOKEN
REPS = "CindySense_AE/RepetitionCount"
SUMMARIES = "CindySense_AE/WorkoutSummary"


def settings():
    return SimpleNamespace(notification_url=URL, notification_token=TOKEN,
                           subscription_origin="CAdmin", repetition_resource=REPS,
                           workout_summary_resource=SUMMARIES)


class FakeCSE:
    def __init__(self):
        self.subscriptions = {}
        self.create_calls = []
        self.fail = False

    async def retrieve(self, path, origin, expected_not_found=False):
        if self.fail:
            raise TinyIoTError("tinyiot_unreachable", "upstream unavailable", 503)
        if path in self.subscriptions:
            return {"m2m:sub": dict(self.subscriptions[path])}
        if any(path == resource for resource in (REPS, SUMMARIES)):
            return {"m2m:cnt": {"rn": path.rsplit("/", 1)[-1]}}
        raise TinyIoTError("resource_not_found", "not found", 404)

    async def embedded_subscriptions(self, resource, origin):
        if self.fail:
            raise TinyIoTError("tinyiot_unreachable", "upstream unavailable", 503)
        prefix = resource + "/"
        return [dict(value) for path, value in self.subscriptions.items()
                if path.startswith(prefix)]

    async def create_subscription(self, resource, origin, name, notification_uri):
        self.create_calls.append(resource + "/" + name)
        self.subscriptions[resource + "/" + name] = {
            "rn": name, "ri": "ri-" + name, "nu": [notification_uri],
            "enc": {"net": [3]}, "nct": 1,
        }
        return {"m2m:sub": self.subscriptions[resource + "/" + name]}


class FakeDiscovery:
    def __init__(self):
        self.calls = []
        self.invalidated = []

    async def discover_subscriptions(self, scope, container_name):
        self.calls.append((scope, container_name))
        from types import SimpleNamespace
        return SimpleNamespace(
            resources=(scope + "/" + container_name + "/RepCount_Sub",),
            diagnostics=SimpleNamespace(
                status=SimpleNamespace(value="discovered"),
                as_dict=lambda: {"status": "discovered", "scope": scope,
                                 "resource_type": 23},
            ),
        )

    def invalidate(self, **kwargs):
        self.invalidated.append(kwargs)


def sub(resource, name, url=URL, net=None):
    return resource + "/" + name, {"rn": name, "ri": "ri-" + name,
        "nu": [url], "enc": {"net": [3] if net is None else net}, "nct": 1}


class SubscriptionMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_and_legacy_subscriptions_are_not_repaired(self):
        cse = FakeCSE()
        cse.subscriptions.update(dict([sub(REPS, "RepCount_Sub"), sub(SUMMARIES, "Summary_Sub")]))
        observed = []
        monitor = SubscriptionMonitor(interval_seconds=1, on_verified=observed.append)
        result = await monitor.check_once(cse, settings(), recover=True)
        self.assertEqual(result["required"]["repetition"]["status"], "healthy")
        self.assertEqual(result["required"]["summary"]["status"], "healthy")
        self.assertEqual(cse.create_calls, [])
        self.assertEqual(observed[0]["repetition"]["subscriptionIds"], ["ri-RepCount_Sub"])

    async def test_discovery_adds_subscription_reference_without_mutation(self):
        cse = FakeCSE()
        cse.subscriptions.update(dict([sub(REPS, "RepCount_Sub"), sub(SUMMARIES, "Summary_Sub")]))
        discovery = FakeDiscovery()
        monitor = SubscriptionMonitor(interval_seconds=1, discovery_resolver=discovery)
        result = await monitor.check_once(cse, settings(), recover=False)
        self.assertEqual(result["verificationStatus"], "verified")
        self.assertEqual(discovery.calls, [("CindySense_AE", "RepetitionCount"),
                                           ("CindySense_AE", "WorkoutSummary")])
        self.assertEqual(cse.create_calls, [])

    async def test_missing_subscriptions_recover_once_and_are_idempotent(self):
        cse = FakeCSE()
        monitor = SubscriptionMonitor(interval_seconds=1)
        await monitor.check_once(cse, settings(), recover=True)
        await monitor.check_once(cse, settings(), recover=True)
        self.assertEqual(cse.create_calls, [REPS + "/cindysense-fastapi-repetitions",
                                             SUMMARIES + "/cindysense-fastapi-summaries"])
        self.assertEqual(monitor.health()["recoverySuccesses"], 2)

    async def test_conflicts_are_reported_and_never_overwritten(self):
        cse = FakeCSE()
        cse.subscriptions.update(dict([sub(REPS, "RepCount_Sub", "http://other/callback"),
                                       sub(SUMMARIES, "Summary_Sub", "http://other/callback")]))
        monitor = SubscriptionMonitor(interval_seconds=1)
        result = await monitor.check_once(cse, settings(), recover=True)
        self.assertEqual(result["conflictCount"], 2)
        self.assertEqual(cse.create_calls, [])
        self.assertEqual(cse.subscriptions[REPS + "/RepCount_Sub"]["nu"], ["http://other/callback"])

    async def test_invalid_event_type_is_a_conflict(self):
        cse = FakeCSE()
        cse.subscriptions.update(dict([sub(REPS, "RepCount_Sub", net=[1]),
                                       sub(SUMMARIES, "Summary_Sub", net=[1])]))
        monitor = SubscriptionMonitor(interval_seconds=1)
        result = await monitor.check_once(cse, settings(), recover=True)
        self.assertEqual(result["conflictCount"], 2)
        self.assertEqual(cse.create_calls, [])

    async def test_outage_uses_sanitized_error_and_backoff(self):
        cse = FakeCSE()
        cse.fail = True
        monitor = SubscriptionMonitor(interval_seconds=2, max_backoff_seconds=8)
        result = await monitor.check_once(cse, settings(), recover=True)
        self.assertEqual(result["lastError"], "tinyiot_unreachable")
        self.assertEqual(result["verificationStatus"], "unavailable")
        self.assertEqual(result["backoffSeconds"], 2)
        self.assertIsNotNone(result["nextRetryAt"])
        self.assertNotIn(TOKEN, str(result))

    async def test_concurrent_recovery_does_not_duplicate_creation(self):
        cse = FakeCSE()
        monitor = SubscriptionMonitor(interval_seconds=1)
        results = await asyncio.gather(*(monitor.check_once(cse, settings(), recover=True)
                                         for _ in range(3)))
        self.assertEqual(cse.create_calls.count(REPS + "/cindysense-fastapi-repetitions"), 1)
        self.assertEqual(cse.create_calls.count(SUMMARIES + "/cindysense-fastapi-summaries"), 1)
        self.assertTrue(all(result["missingSubscriptionCount"] == 0 for result in results))

    async def test_disabled_monitor_does_not_call_cse(self):
        cse = FakeCSE()
        monitor = SubscriptionMonitor(enabled=False)
        result = await monitor.check_once(cse, settings())
        self.assertFalse(result["enabled"])
        self.assertEqual(result["lastError"], None)

    async def test_monitor_starts_and_stops_gracefully(self):
        cse = FakeCSE()
        monitor = SubscriptionMonitor(interval_seconds=60)
        task = await monitor.start(cse, settings())
        await asyncio.sleep(0)
        await monitor.stop()
        self.assertTrue(task.done())
        self.assertIsNone(monitor._task)


if __name__ == "__main__":
    unittest.main()
