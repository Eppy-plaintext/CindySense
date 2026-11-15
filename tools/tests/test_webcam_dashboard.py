import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.webcam_dashboard import CameraLock, DashboardIPC, DashboardPreview, atomic_write
from tools.webcam_exercises import CindyWorkout, positioning_feedback
from tools.workout_summary import completed_summary


class DashboardIPCTests(unittest.TestCase):
    def test_preview_worker_publishes_latest_frame_outside_detector(self):
        class FakeDashboard:
            def __init__(self):
                self.frames = []
                self.statuses = []
            def frame(self, jpeg):
                self.frames.append(jpeg)
            def status(self, **values):
                self.statuses.append(values)

        class FakeFrame:
            def copy(self):
                return self

        dashboard = FakeDashboard()
        jpeg = type('Jpeg', (), {'tobytes': lambda self: b'jpeg'})()
        with patch('tools.webcam_dashboard.cv2.imencode', return_value=(True, jpeg)):
            preview = DashboardPreview(dashboard, interval=0.01)
            try:
                preview.offer(FakeFrame(), {'state': 'running'})
                preview.offer(FakeFrame(), {'state': 'running'})
                deadline = time.monotonic() + 1
                while not dashboard.frames and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(dashboard.frames)
                self.assertTrue(dashboard.statuses)
                self.assertGreaterEqual(preview.diagnostics()['previewDroppedFrames'], 0)
            finally:
                preview.close()

    def test_positioning_feedback_is_conservative_and_non_gating(self):
        point = lambda y, score=0.95: SimpleNamespace(x=0.5, y=y, visibility=score)
        full = [point(0.0) for _ in range(33)]
        for index, y in {0: 0.1, 11: 0.25, 12: 0.25, 13: 0.35, 14: 0.35,
                         15: 0.45, 16: 0.45, 23: 0.6, 24: 0.6, 27: 0.8, 28: 0.8}.items():
            full[index] = point(y)
        self.assertEqual(positioning_feedback('pushup', full), 'Ready for detection')
        full[16] = point(0.45, 0.1)
        self.assertEqual(positioning_feedback('pushup', full), 'Required joints not visible')
    @unittest.skipUnless(os.name == 'nt', 'Windows file replacement contention')
    def test_status_write_retries_transient_windows_access_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            path.write_bytes(b'old complete status')
            original_replace = os.replace
            attempts = []

            def temporarily_locked(source, destination):
                attempts.append((source, destination))
                if len(attempts) == 1:
                    self.assertEqual(path.read_bytes(), b'old complete status')
                    raise PermissionError(13, 'Access is denied')
                return original_replace(source, destination)

            with patch('tools.webcam_dashboard.os.replace', side_effect=temporarily_locked), \
                    patch('tools.webcam_dashboard.time.sleep'):
                atomic_write(path, b'new complete status')

            self.assertEqual(len(attempts), 2)
            self.assertEqual(path.read_bytes(), b'new complete status')

    @unittest.skipUnless(os.name == 'nt', 'Windows file replacement contention')
    def test_persistent_status_write_error_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            path.write_bytes(b'old complete status')
            with patch('tools.webcam_dashboard.os.replace', side_effect=PermissionError(13, 'Access is denied')) \
                    as replace, patch('tools.webcam_dashboard.time.sleep'):
                with self.assertRaises(PermissionError):
                    atomic_write(path, b'new status')
            self.assertEqual(replace.call_count, 10)
            self.assertEqual(path.read_bytes(), b'old complete status')

    def test_frames_state_stop_and_camera_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            ipc = DashboardIPC(folder)
            ipc.status(state='running', sessionId='webcam-20261111T000000Z-12345678')
            ipc.frame(b'\xff\xd8real-frame\xff\xd9')
            self.assertEqual(json.loads(ipc.status_path.read_text())['state'], 'running')
            self.assertGreater(json.loads(ipc.status_path.read_text())['pid'], 0)
            self.assertIn('updatedAt', json.loads(ipc.status_path.read_text()))
            self.assertEqual(ipc.frame_path.read_bytes(), b'\xff\xd8real-frame\xff\xd9')
            self.assertFalse(ipc.stop_requested())
            ipc.stop_path.touch()
            self.assertTrue(ipc.stop_requested())
            first = CameraLock(Path(folder) / 'camera.lock')
            try:
                with self.assertRaisesRegex(RuntimeError, 'already owns'):
                    CameraLock(Path(folder) / 'camera.lock')
            finally:
                first.close()
            second = CameraLock(Path(folder) / 'camera.lock')
            second.close()

    def test_authoritative_timer_yields_one_completed_summary(self):
        tick = [0.0]
        workout = CindyWorkout(1200, clock=lambda: tick[0])
        for exercise, target in (('pullup', 5), ('pushup', 10), ('squat', 15)):
            for _ in range(target):
                workout.record_rep(exercise)
        self.assertEqual(workout.rounds, 1)
        tick[0] = 1200.0
        self.assertTrue(workout.finished)
        summary = completed_summary('webcam-20261111T000000Z-12345678', workout,
                                    {'pullup': 5, 'pushup': 10, 'squat': 15}, 'timer')
        self.assertEqual((summary['rounds'], summary['durationSeconds'], summary['completionReason']),
                         (1, 1200, 'timer'))
