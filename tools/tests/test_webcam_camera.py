import threading
import time
import unittest
from queue import Empty, Queue
from unittest.mock import patch

from tools.webcam_camera import LatestFrameCamera, is_stale_frame


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


class ControlledCapture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.frames = Queue()

    def read(self):
        try:
            return True, self.frames.get(timeout=0.05)
        except Empty:
            return False, None


class PausingCapture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.frames = Queue()

    def read(self):
        return True, self.frames.get(timeout=3)


class CameraBufferTests(unittest.TestCase):
    def test_consumer_timeout_does_not_mean_camera_has_failed(self):
        capture = PausingCapture()
        capture.frames.put(1)
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            camera.start(timeout=1)
            try:
                self.assertEqual(camera.read(timeout=1)[1], 1)
                self.assertFalse(camera.read(timeout=0.01)[0])
                self.assertIsNone(camera.terminal_error())
                capture.frames.put(2)
                self.assertEqual(camera.read(timeout=1)[1], 2)
                self.assertEqual(camera.diagnostics()['readFailures'], 0)
            finally:
                capture.frames.put(3)
                camera.close()
    def test_stale_frame_threshold_is_bounded(self):
        self.assertFalse(is_stale_frame(10.0, 10.249))
        self.assertTrue(is_stale_frame(10.0, 10.251))

    def test_slow_consumer_gets_newest_frame_without_backlog(self):
        capture = ControlledCapture()
        capture.frames.put(1)
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            camera.start(timeout=1)
            try:
                ok, frame, first = camera.read(timeout=1)
                self.assertTrue(ok)
                self.assertEqual(frame, 1)
                for value in range(2, 7):
                    capture.frames.put(value)
                deadline = time.monotonic() + 1
                while camera.diagnostics()["sequence"] < 6 and time.monotonic() < deadline:
                    time.sleep(0.001)
                ok, frame, latest = camera.read(timeout=1)
                self.assertTrue(ok)
                self.assertEqual(frame, 6)
                self.assertEqual(camera.diagnostics()["droppedFrames"], 4)
                self.assertGreaterEqual(latest["capturedAt"], first["capturedAt"])
                self.assertIn("capturedAtUtc", latest)
            finally:
                camera.close()

    def test_consumed_frames_are_not_reported_as_dropped(self):
        capture = ControlledCapture()
        capture.frames.put(1)
        with patch("tools.webcam_camera.cv2.VideoCapture", return_value=capture):
            camera = LatestFrameCamera()
            camera.start(timeout=1)
            try:
                self.assertEqual(camera.read(timeout=1)[1], 1)
                capture.frames.put(2)
                self.assertEqual(camera.read(timeout=1)[1], 2)
                self.assertEqual(camera.diagnostics()["droppedFrames"], 0)
                capture.frames.put(3)
                capture.frames.put(4)
                deadline = time.monotonic() + 1
                while camera.diagnostics()["sequence"] < 4 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertEqual(camera.read(timeout=1)[1], 4)
                self.assertEqual(camera.diagnostics()["droppedFrames"], 1)
                self.assertIsNotNone(camera.diagnostics()["captureFps"])
                self.assertGreater(camera.diagnostics()["readMaxMs"], 0)
                self.assertGreater(camera.diagnostics()["captureIntervalMaxMs"], 0)
                self.assertIn(camera.diagnostics()["queueDepth"], (0, 1))
            finally:
                camera.close()

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
