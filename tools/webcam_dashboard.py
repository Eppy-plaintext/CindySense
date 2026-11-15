"""Local, single-host control and preview exchange for the webcam runner."""

import atexit
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2


def atomic_write(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with open(temporary, 'wb') as handle:
        handle.write(content)
    # Windows readers can briefly prevent replacement of an open status/frame file.
    # Keep the last complete file visible while retrying the atomic swap.
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == 9:
                raise
            time.sleep(0.01 * (attempt + 1))


class CameraLock:
    """Prevent both dashboard and manually launched runners from opening the camera."""

    def __init__(self, path):
        self.handle = open(path, 'a+b')
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError('Another CindySense webcam worker already owns the camera.') from None
        atexit.register(self.close)

    def close(self):
        if self.handle.closed:
            return
        self.handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


class DashboardIPC:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.status_path = self.directory / 'status.json'
        self.frame_path = self.directory / 'frame.jpg'
        self.stop_path = self.directory / 'stop'

    def stop_requested(self):
        return self.stop_path.exists()

    def status(self, **values):
        values.update(pid=os.getpid(), updatedAt=datetime.now(timezone.utc).isoformat())
        atomic_write(self.status_path, json.dumps(values, separators=(',', ':')).encode('utf-8'))

    def frame(self, jpeg):
        atomic_write(self.frame_path, jpeg)


class DashboardPreview:
    """Encode and publish only the newest preview outside the detector loop."""

    def __init__(self, dashboard, interval=0.08):
        self.dashboard = dashboard
        self.interval = interval
        self.condition = threading.Condition()
        self.latest = None
        self.stopping = False
        self.frames = 0
        self.dropped = 0
        self.encode_ms = 0.0
        self.write_ms = 0.0
        self.last_age_ms = None
        self.thread = threading.Thread(target=self._run, name='webcam-preview', daemon=True)
        self.thread.start()

    def offer(self, frame, values, captured_at=None):
        with self.condition:
            if self.latest is not None:
                self.dropped += 1
            self.latest = (frame.copy(), dict(values), captured_at or time.monotonic())
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                while self.latest is None and not self.stopping:
                    self.condition.wait(timeout=0.5)
                if self.latest is None and self.stopping:
                    return
                frame, values, captured_at = self.latest
                self.latest = None
            started = time.monotonic()
            encoded, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            self.encode_ms += (time.monotonic() - started) * 1000
            self.last_age_ms = (time.monotonic() - captured_at) * 1000
            if encoded:
                started = time.monotonic()
                self.dashboard.frame(jpeg.tobytes())
                self.write_ms += (time.monotonic() - started) * 1000
                self.frames += 1
                self.dashboard.status(**values)
            time.sleep(self.interval)

    def diagnostics(self):
        return {"previewFrames": self.frames, "previewDroppedFrames": self.dropped,
                "previewEncodeAvgMs": round(self.encode_ms / max(1, self.frames), 2),
                "previewWriteAvgMs": round(self.write_ms / max(1, self.frames), 2),
                "previewFrameAgeMs": round(self.last_age_ms, 2) if self.last_age_ms is not None else None}

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join(timeout=3.0)
