"""Camera capture with a bounded latest-frame buffer.

The capture thread owns ``VideoCapture``. Consumers receive only the newest
frame, so a slow pose inference pass cannot make the detector process stale
frames accumulated in an OpenCV/backend buffer.
"""

import sys
import threading
import time

import cv2


class LatestFrameCamera:
    def __init__(self, index=0, width=640, height=480, fps=30):
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self._capture = None
        self._thread = None
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest = None
        self._sequence = 0
        self._consumed_sequence = 0
        self._error = None
        self._frames = 0
        self._dropped = 0

    def start(self, timeout=5.0):
        capture = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if sys.platform.startswith("win") else cv2.VideoCapture(self.index)
        if not capture.isOpened() and sys.platform.startswith("win"):
            capture.release()
            capture = cv2.VideoCapture(self.index)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError("Cannot open webcam. Check Windows camera permissions and camera availability.")
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        self._capture = capture
        self._thread = threading.Thread(target=self._run, name="cindy-camera", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._sequence == 0 and self._error is None and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.01, deadline - time.monotonic()))
        if self._error is not None:
            self.close()
            raise RuntimeError(self._error)
        if self._sequence == 0:
            self.close()
            raise RuntimeError("Webcam did not deliver a frame during startup.")
        print(f"[CAMERA] width={capture.get(cv2.CAP_PROP_FRAME_WIDTH):.0f} "
              f"height={capture.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f} "
              f"fps={capture.get(cv2.CAP_PROP_FPS):.1f} "
              f"buffer={capture.get(cv2.CAP_PROP_BUFFERSIZE):.0f}")

    def _run(self):
        capture = self._capture
        try:
            while not self._stop.is_set():
                ok, frame = capture.read()
                captured_at = time.monotonic()
                if not ok:
                    with self._condition:
                        self._error = "Camera frame read failed."
                        self._condition.notify_all()
                    return
                with self._condition:
                    if self._latest is not None:
                        self._dropped += 1
                    self._sequence += 1
                    self._latest = (self._sequence, captured_at, frame)
                    self._frames += 1
                    self._condition.notify_all()
        finally:
            capture.release()

    def read(self, timeout=2.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._sequence <= self._consumed_sequence and self._error is None and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, None, None
                self._condition.wait(timeout=remaining)
            if self._error is not None:
                return False, None, None
            if self._latest is None:
                return False, None, None
            sequence, captured_at, frame = self._latest
            self._consumed_sequence = sequence
            return True, frame, {"sequence": sequence, "capturedAt": captured_at}

    def diagnostics(self):
        with self._condition:
            return {"frames": self._frames, "droppedFrames": self._dropped,
                    "sequence": self._sequence}

    def close(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._thread is None or not self._thread.is_alive():
            self._thread = None
            self._capture = None
