"""Offline TS integration tests; no live TinyIoT writes."""

import copy
import unittest

from tools.webcam_tinyiot import TinyIoTConnectionError, TinyIoTError
from tools.workout_timeseries import TS_MBS, TS_MNI, TS_PATH, WorkoutTimeSeries
from tools.workout_ts_integrity import tsi_name


WORKOUT_ID = "session-1"
SOURCE = "CindySense_AE/WorkoutSummary/webcam-summary-session-1"


class FakeClient:
    def __init__(self):
        self.resources = {
            "/" + SOURCE: {"m2m:cin": {
                "rn": "webcam-summary-session-1", "ct": "20261111T120000,123",
                "con": {"sessionId": WORKOUT_ID, "source": "webcam", "status": "completed",
                        "rounds": 1, "durationSeconds": 1200,
                        "pullups": 5, "pushups": 10, "squats": 15}}}}
        self.posts = []
        self.lose_reply = False
        self.fail_tsi = False

    def _request(self, method, path, body=None, ty=None):
        if method == "GET":
            value = self.resources.get(path)
            return (200, "2000", copy.deepcopy(value)) if value else (404, "4004", {})
        assert method == "POST"
        self.posts.append((path, ty))
        if ty == 30 and self.fail_tsi:
            return 503, "5000", {}
        key = "m2m:ts" if ty == 29 else "m2m:tsi"
        resource = copy.deepcopy(body[key])
        resource["ty"] = ty
        full = path + "/" + resource["rn"]
        if full in self.resources:
            return 409, "4105", {}
        self.resources[full] = {key: resource}
        if self.lose_reply:
            self.lose_reply = False
            raise TinyIoTConnectionError("reply lost")
        return 201, "2001", {}


class WorkoutTimeSeriesTests(unittest.TestCase):
    def test_provision_mirror_and_retry_do_not_duplicate(self):
        client = FakeClient()
        series = WorkoutTimeSeries(client)
        self.assertEqual(series.provision()[0], "created")
        self.assertEqual(series.provision()[0], "reused")
        self.assertEqual(client.resources["/" + TS_PATH]["m2m:ts"]["mni"], TS_MNI)
        self.assertEqual(client.resources["/" + TS_PATH]["m2m:ts"]["mbs"], TS_MBS)
        self.assertEqual(series.mirror_source(SOURCE), ("created", WORKOUT_ID))
        self.assertEqual(series.mirror_source(SOURCE), ("verified", WORKOUT_ID))
        self.assertEqual(client.posts.count(("/" + TS_PATH, 30)), 1)
        self.assertIn("/" + TS_PATH + "/" + tsi_name(WORKOUT_ID), client.resources)

    def test_lost_reply_is_reconciled_by_readback(self):
        client = FakeClient()
        series = WorkoutTimeSeries(client)
        series.provision()
        client.lose_reply = True
        self.assertEqual(series.mirror_source(SOURCE), ("verified", WORKOUT_ID))
        self.assertEqual(series.mirror_source(SOURCE), ("verified", WORKOUT_ID))
        self.assertEqual(client.posts.count(("/" + TS_PATH, 30)), 1)

    def test_failure_and_conflict_fail_closed_without_changing_summary(self):
        client = FakeClient()
        series = WorkoutTimeSeries(client)
        series.provision()
        original = copy.deepcopy(client.resources["/" + SOURCE])
        client.fail_tsi = True
        with self.assertRaises(TinyIoTError):
            series.mirror_source(SOURCE)
        self.assertEqual(client.resources["/" + SOURCE], original)
        client.fail_tsi = False
        series.mirror_source(SOURCE)
        child = client.resources["/" + TS_PATH + "/" + tsi_name(WORKOUT_ID)]
        child["m2m:tsi"]["dgt"] = "20261111T130000"
        with self.assertRaises(TinyIoTError):
            series.mirror_source(SOURCE)


if __name__ == "__main__":
    unittest.main()
