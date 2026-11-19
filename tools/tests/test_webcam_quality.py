import unittest

from tools.webcam_quality import TrackingQuality


class TrackingQualityTests(unittest.TestCase):
    def test_session_exercises_are_independent_and_initially_insufficient(self):
        quality = TrackingQuality(minimum_frames=2)
        quality.record('pullup', has_pose=True, usable_measurement=True, detector_gap_ms=None)
        self.assertEqual(quality.snapshot()['pullup']['state'], 'insufficient_evidence')
        quality.record('pullup', has_pose=True, usable_measurement=True, detector_gap_ms=40)
        self.assertEqual(quality.snapshot()['pullup']['state'], 'stable')
        quality.record('squat', has_pose=False, usable_measurement=False, detector_gap_ms=200)
        self.assertEqual(quality.snapshot()['pullup']['state'], 'stable')
        self.assertEqual(quality.snapshot()['squat']['state'], 'insufficient_evidence')

    def test_drops_gaps_and_camera_interruptions_degrade_diagnostics(self):
        quality = TrackingQuality(minimum_frames=1)
        quality.record('pushup', has_pose=True, usable_measurement=True,
                       detector_gap_ms=120, dropped_frames=2)
        quality.interruption('pushup')
        item = quality.snapshot()['pushup']
        self.assertEqual(item['state'], 'degraded')
        self.assertEqual(item['longDetectorGaps'], 1)
        self.assertEqual(item['droppedFrames'], 2)
        self.assertEqual(item['cameraInterruptions'], 1)


if __name__ == '__main__':
    unittest.main()
