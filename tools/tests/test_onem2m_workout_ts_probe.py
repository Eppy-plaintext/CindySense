"""Offline safety checks for the opt-in workout TS probe."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import onem2m_workout_ts_probe as probe


class WorkoutTSProbeTests(unittest.TestCase):
    def test_fingerprint_compares_without_returning_private_subscription_values(self):
        first = {"m2m:sub": {"nu": ["http://callback/private-token"], "enc": {"net": [3]}}}
        second = {"m2m:sub": {"nu": ["http://callback/different-token"], "enc": {"net": [3]}}}
        digest = probe.fingerprint(first)
        self.assertEqual(len(digest), 64)
        self.assertNotIn("private-token", digest)
        self.assertNotEqual(digest, probe.fingerprint(second))

    def test_timeout_after_root_creation_stops_without_cleanup_requests(self):
        summary = {"m2m:cin": {
            "ri": "source-ri", "ct": "20261111T042142,495",
            "con": {"sessionId": probe.WORKOUT_ID, "status": "completed",
                    "rounds": 1, "durationSeconds": 1200,
                    "pullups": 5, "pushups": 10, "squats": 15,
                    "source": "webcam"}}}
        calls = []

        def fake_request(method, path, body=None, ty=None):
            calls.append((method, path))
            if path.endswith("/WorkoutSummary/la"):
                return 200, "2000", summary
            if method == "POST":
                return 201, "2001", {"m2m:cnt": {}}
            if method == "GET" and path == probe.PARENT and any(
                    earlier[0] == "POST" for earlier in calls):
                raise TimeoutError("CSE stopped responding")
            return 200, "2000", {"resource": path}

        output = io.StringIO()
        with patch.object(probe, "request", side_effect=fake_request), \
             patch.object(probe, "api_status", return_value=200), \
             redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "unresponsive"):
                probe.main()
        report = json.loads(output.getvalue())
        self.assertTrue(report["leftover_test_subtree"])
        self.assertTrue(report["cse_unresponsive"])
        self.assertFalse(any(method == "DELETE" for method, _ in calls))


if __name__ == "__main__":
    unittest.main()
