"""Camera capture with a bounded latest-frame buffer.

The capture thread owns ``VideoCapture``. Consumers receive only the newest
frame, so a slow pose inference pass cannot make the detector process stale
frames accumulated in an OpenCV/backend buffer. Capture timestamps mark when
``read()`` returns, not sensor exposure; they cannot establish sensor frame age.
"""

import sys
import threading
import time
from datetime import datetime, timezone

import cv2


def is_stale_frame(captured_at, inference_started_at, threshold_ms=250):
    """Flag time held after read() returns; sensor frame age is unavailable."""
    return (inference_started_at - captured_at) * 1000 > threshold_ms


class LatestFrameCamera:
    # Tolerate short USB/driver hiccups; stop only after a bounded failure run.
    MAX_CONSECUTIVE_READ_FAILURES = 30

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
        self._first_captured_at = None
        self._last_captured_at = None
        self._read_failures = 0
        self._consecutive_read_failures = 0
        self._recoveries = 0
        self._read_duration_total_ms = 0.0
        self._read_duration_max_ms = 0.0
        self._capture_interval_total_ms = 0.0
        self._capture_interval_max_ms = 0.0
        self._capture_intervals = 0

    def start(self, timeout=5.0):
        capture = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if sys.platform.startswith("win") else cv2.VideoCapture(self.index)
        if not capture.isOpened() and sys.platform.startswith("win"):
            capture.release()
            capture = cv2.VideoCapture(self.index)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError("Cannot open webcam. Check Windows camera permissions and camera availability.")
        buffer_set = capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
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
        try:
            backend_name = capture.getBackendName()
        except (AttributeError, cv2.error):
            backend_name = 'unknown'
        print(f"[CAMERA] backend={backend_name} "
              f"width={capture.get(cv2.CAP_PROP_FRAME_WIDTH):.0f} "
              f"height={capture.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f} "
              f"fps={capture.get(cv2.CAP_PROP_FPS):.1f} "
              f"buffer={capture.get(cv2.CAP_PROP_BUFFERSIZE):.0f} "
              f"bufferSet={bool(buffer_set)}")

    def _run(self):
        capture = self._capture
        try:
            while not self._stop.is_set():
                read_started = time.monotonic()
                ok, frame = capture.read()
                captured_at = time.monotonic()
                read_ms = (captured_at - read_started) * 1000
                captured_utc = datetime.now(timezone.utc).isoformat(timespec='milliseconds') if ok else None
                if not ok:
                    with self._condition:
                        self._read_duration_total_ms += read_ms
                        self._read_duration_max_ms = max(self._read_duration_max_ms, read_ms)
                        self._read_failures += 1
                        self._consecutive_read_failures += 1
                        if self._consecutive_read_failures >= self.MAX_CONSECUTIVE_READ_FAILURES:
                            self._error = "Camera frame read failed repeatedly."
                        self._condition.notify_all()
                    if self._error is not None:
                        return
                    time.sleep(0.05)
                    continue
                with self._condition:
                    self._read_duration_total_ms += read_ms
                    self._read_duration_max_ms = max(self._read_duration_max_ms, read_ms)
                    if self._last_captured_at is not None:
                        interval_ms = (captured_at - self._last_captured_at) * 1000
                        self._capture_interval_total_ms += interval_ms
                        self._capture_interval_max_ms = max(self._capture_interval_max_ms, interval_ms)
                        self._capture_intervals += 1
                    if self._consecutive_read_failures:
                        self._recoveries += 1
                        self._consecutive_read_failures = 0
                    # A consumed frame may remain in the slot until the next
                    # capture; replacing it is not a dropped unread frame.
                    if self._sequence > self._consumed_sequence:
                        self._dropped += 1
                    self._sequence += 1
                    self._latest = (self._sequence, captured_at, captured_utc, frame)
                    if self._first_captured_at is None:
                        self._first_captured_at = captured_at
                    self._last_captured_at = captured_at
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
            sequence, captured_at, captured_utc, frame = self._latest
            self._consumed_sequence = sequence
            return True, frame, {"sequence": sequence, "capturedAt": captured_at,
                                 "capturedAtUtc": captured_utc}

    def terminal_error(self):
        """Distinguish a recoverable consumer timeout from a stopped capture thread."""
        with self._condition:
            return (self._error or ("Camera capture stopped." if self._stop.is_set() else None)
                    or ("Camera capture thread exited." if self._thread is not None
                        and not self._thread.is_alive() else None))

    def diagnostics(self):
        with self._condition:
            span = ((self._last_captured_at - self._first_captured_at)
                    if self._first_captured_at is not None and self._last_captured_at is not None else 0)
            return {"frames": self._frames, "droppedFrames": self._dropped,
                    "captureFps": round((self._frames - 1) / span, 2) if span > 0 else None,
                    "sequence": self._sequence,
                    "readAvgMs": (round(self._read_duration_total_ms /
                                        max(1, self._frames + self._read_failures), 2)),
                    "readMaxMs": round(self._read_duration_max_ms, 2),
                    "captureIntervalAvgMs": (round(self._capture_interval_total_ms /
                                                   self._capture_intervals, 2)
                                             if self._capture_intervals else None),
                    "captureIntervalMaxMs": round(self._capture_interval_max_ms, 2),
                    "queueDepth": int(self._sequence > self._consumed_sequence),
                    "readFailures": self._read_failures,
                    "consecutiveReadFailures": self._consecutive_read_failures,
                    "recoveries": self._recoveries}

    def close(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._thread is None or not self._thread.is_alive():
            self._thread = None
            self._capture = None
