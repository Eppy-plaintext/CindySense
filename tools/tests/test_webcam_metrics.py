import unittest

from tools.webcam_metrics import WebcamTimings


class WebcamTimingsTests(unittest.TestCase):
    def test_bounded_summary_and_gap_threshold(self):
        timings = WebcamTimings(gap_threshold_ms=100, samples_per_stage=3)
        for value in (20, 80, 100, 120, 240):
            timings.record('detectorGapMs', value)
        summary = timings.snapshot()
        self.assertEqual(summary['detectorGapsOverThreshold'], 2)
        self.assertEqual(summary['detectorGapMs']['count'], 5)
        self.assertEqual(summary['detectorGapMs']['average'], 112)
        self.assertEqual(summary['detectorGapMs']['maximum'], 240)
        self.assertEqual(summary['detectorGapMs']['recentP95'], 240)
        self.assertEqual(len(timings.samples['detectorGapMs']), 3)

    def test_invalid_thresholds_and_durations_are_rejected(self):
        with self.assertRaises(ValueError):
            WebcamTimings(0)
        with self.assertRaises(ValueError):
            WebcamTimings(samples_per_stage=0)
        with self.assertRaises(ValueError):
            WebcamTimings().record('poseMs', -1)


if __name__ == '__main__':
    unittest.main()
