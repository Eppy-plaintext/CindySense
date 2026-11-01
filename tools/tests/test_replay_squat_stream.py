import sys
import unittest
from unittest.mock import patch

from tools import replay_squat_stream


class ReplayCliTests(unittest.TestCase):
    def test_standing_calibration_seconds_cli_option(self):
        result = {
            "samples": 0,
            "batches": 0,
            "state": {
                "calibration": "ready",
                "orientation": "undetermined",
                "repetitions": 0,
                "calibration_quality": {"mode": "initial_standing",
                                        "counts_enabled": True},
                "standing_reference_deg": 0.0,
                "diagnostics": {},
                "recent_rejections": [],
                "uncertain_candidates": [],
            },
            "events": [],
            "first_time": 0.0,
            "mean_bottom_to_confirmation_seconds": 0.0,
            "mean_batch_wait_seconds": 0.0,
            "max_batch_wait_seconds": 0.0,
        }
        argv = [
            "replay_squat_stream.py", "recording.csv",
            "--calibration-mode", "initial_standing",
            "--standing-calibration-seconds", "5",
        ]
        with patch.object(sys, "argv", argv), \
             patch.object(replay_squat_stream, "replay", return_value=result) as replay:
            replay_squat_stream.main()
        self.assertEqual(replay.call_args.kwargs["calibration_mode"], "initial_standing")
        self.assertEqual(replay.call_args.kwargs["standing_calibration_seconds"], 5.0)


if __name__ == "__main__":
    unittest.main()
