"""Synthetic regression tests; no live TinyIoT writes or workout fixture claims."""

import json
import unittest

from tools.workout_ts_integrity import tsi_body, verified_workout, verify_tsi_readback


WORKOUT = "final-summary-097c0172"
COUNTS = {"pullup": 5, "pushup": 10, "squat": 15}


def source_cin(**changes):
    summary = {"sessionId": WORKOUT, "source": "webcam", "status": "completed",
               "pullups": 5, "pushups": 10, "squats": 15,
               "rounds": 1, "durationSeconds": 1200}
    summary.update(changes)
    return {"m2m:cin": {"ct": "20261111T100000,123456", "con": summary}}


class WorkoutTSIntegrityTests(unittest.TestCase):
    def test_verified_summary_becomes_one_lossless_tsi(self):
        verified = verified_workout(source_cin(), WORKOUT, COUNTS, 1, 1200)
        body = tsi_body(verified)
        self.assertEqual(body["m2m:tsi"]["dgt"], "20261111T100000,123456")
        self.assertEqual(json.loads(body["m2m:tsi"]["con"])["reps"], COUNTS)
        self.assertEqual(json.loads(body["m2m:tsi"]["con"])["sourceCinCreatedAt"],
                         "20261111T100000,123456")
        verify_tsi_readback({"m2m:tsi": body["m2m:tsi"]}, verified)

    def test_rejects_wrong_count_and_workout_identity(self):
        for changed in ({"sessionId": "other"}, {"workoutId": "other"},
                        {"pushups": 8},
                        {"rounds": 0}, {"durationSeconds": 1199},
                        {"source": "mpu6050"}, {"status": None},
                        {"status": "in-progress"},
                        {"reps": {"pullup": 6, "pushup": 10, "squat": 15}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verified_workout(source_cin(**changed), WORKOUT, COUNTS, 1, 1200)

    def test_rejects_invalid_timestamp_and_changed_tsi_content(self):
        source = source_cin()
        source["m2m:cin"]["ct"] = "not-a-timestamp"
        with self.assertRaises(ValueError):
            verified_workout(source, WORKOUT, COUNTS, 1, 1200)
        verified = verified_workout(source_cin(), WORKOUT, COUNTS, 1, 1200)
        body = tsi_body(verified)
        body["m2m:tsi"]["con"] = json.dumps({"workoutId": WORKOUT, "reps": {}})
        with self.assertRaises(ValueError):
            verify_tsi_readback(body, verified)


if __name__ == "__main__":
    unittest.main()
