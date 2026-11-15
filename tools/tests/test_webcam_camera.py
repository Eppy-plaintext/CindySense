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


if __name__ == "__main__":
    unittest.main()
