import threading
import time
import unittest
from unittest.mock import patch

from tools.webcam_camera import LatestFrameCamera


class FakeCapture:
    def __init__(self):
        self.released = threading.Event()
        self.value = 0

    def isOpened(self):
        return True

    def set(self, *_args):
        return True

    def get(self, *_args):
        return 0

    def read(self):
        self.value += 1
        time.sleep(0.001)
        return True, self.value

    def release(self):
        self.released.set()


class RecoveringCapture(FakeCapture):
    def __init__(self, failures=2):
        super().__init__()
        self.failures = failures

    def read(self):
        self.value += 1
        if self.value <= self.failures:
            return False, None
        return True, self.value


class FailingCapture(FakeCapture):
    def read(self):
        return False, None


class CameraBufferTests(unittest.TestCase):
    def test_consumer_receives_newest_frame_and_drops_stale_frames(self):
        capture = FakeCapture()
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            camera.start(timeout=1)
            try:
                time.sleep(0.02)
                ok, frame, info = camera.read(timeout=1)
                self.assertTrue(ok)
                self.assertEqual(frame, info["sequence"])
                self.assertGreater(camera.diagnostics()["droppedFrames"], 0)
                second_ok, second_frame, second_info = camera.read(timeout=1)
                self.assertTrue(second_ok)
                self.assertGreater(second_info["sequence"], info["sequence"])
                self.assertGreater(second_frame, frame)
            finally:
                camera.close()
        self.assertTrue(capture.released.wait(1))

    def test_start_reports_camera_open_failure(self):
        capture = FakeCapture()
        capture.isOpened = lambda: False
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            with self.assertRaises(RuntimeError):
                LatestFrameCamera().start(timeout=0.1)

    def test_transient_read_failure_recovers_without_ending_capture(self):
        capture = RecoveringCapture(failures=2)
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            camera.start(timeout=2)
            try:
                ok, frame, _ = camera.read(timeout=1)
                self.assertTrue(ok)
                self.assertGreaterEqual(frame, 3)
                diagnostics = camera.diagnostics()
                self.assertEqual(diagnostics["readFailures"], 2)
                self.assertEqual(diagnostics["recoveries"], 1)
                self.assertEqual(diagnostics["consecutiveReadFailures"], 0)
            finally:
                camera.close()

    def test_prolonged_read_failure_is_bounded_and_reported(self):
        capture = FailingCapture()
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            with self.assertRaisesRegex(RuntimeError, "did not deliver"):
                camera.start(timeout=0.2)
            self.assertGreaterEqual(camera.diagnostics()["readFailures"], 1)
            camera.close()


if __name__ == "__main__":
    unittest.main()
