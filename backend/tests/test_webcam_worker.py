import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from backend.webcam_worker import WebcamWorker
from backend import main


class FakeProcess:
    def __init__(self):
        self.exit_code = None
        self.terminated = False

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        self.exit_code = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.exit_code = -1


class WebcamWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.commands = []
        self.processes = []

        def launch(command, **kwargs):
            self.commands.append(command)
            process = FakeProcess()
            self.processes.append(process)
            return process

        self.worker = WebcamWorker(process_factory=launch, state_dir=self.temp.name)

    def tearDown(self):
        self.worker.shutdown()
        if self.worker.directory:
            shutil.rmtree(self.worker.directory, ignore_errors=True)
        self.temp.cleanup()

    def start(self):
        return self.worker.start(python=sys.executable, event_origin='CWebcamPublisher',
                                 summary_origin='CWebcamSummary', base_url='http://127.0.0.1:3001/TinyIoT')

    def test_start_is_singleton_and_uses_existing_detector(self):
        self.assertEqual(self.start()['state'], 'starting')
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.start())) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.commands), 1)
        self.assertTrue(all(result['state'] == 'starting' for result in results))
        command = self.commands[0]
        self.assertTrue(command[1].endswith('tools\\webcam_squat.py') or command[1].endswith('tools/webcam_squat.py'))
        self.assertIn('--dashboard-ipc', command)
        self.assertIn('--tinyiot', command)
        self.assertEqual(command[command.index('--duration-minutes') + 1], '20')

    def test_stop_signals_runner_and_preserves_completed_status(self):
        self.start()
        self.assertEqual(self.worker.request_stop()['state'], 'stopping')
        self.assertTrue((self.worker.directory / 'stop').exists())
        (self.worker.directory / 'status.json').write_text(json.dumps({
            'state': 'completed', 'sessionId': 'webcam-20261111T000000Z-12345678',
            'summaryPublication': 'created'}), encoding='utf-8')
        self.assertEqual(self.worker.status()['state'], 'stopping')
        self.processes[0].exit_code = 0
        self.assertEqual(self.worker.status()['state'], 'stopped')
        self.assertEqual(self.worker.status()['summaryPublication'], 'created')

    def test_status_reconnects_and_uncertain_worker_blocks_new_start(self):
        self.start()
        (self.worker.directory / 'status.json').write_text(json.dumps({
            'state': 'running', 'pid': 1234,
            'updatedAt': '2026-11-11T00:00:00+00:00'}), encoding='utf-8')
        recovered = WebcamWorker(state_dir=self.temp.name)
        with patch('backend.webcam_worker._pid_confirmed_gone', return_value=False):
            self.assertEqual(recovered.status()['state'], 'error')
            self.assertEqual(recovered.status()['pid'], 1234)
            with self.assertRaisesRegex(ValueError, 'ownership is uncertain'):
                recovered.start(python=sys.executable, event_origin='CWebcamPublisher',
                                base_url='http://127.0.0.1:3001/TinyIoT')
        recovered.request_stop()
        self.assertTrue((recovered.directory / 'stop').exists())

    def test_recovered_crashed_worker_allows_restart_only_when_pid_is_gone(self):
        self.start()
        (self.worker.directory / 'status.json').write_text(json.dumps({
            'state': 'running', 'pid': 1234,
            'updatedAt': '2026-11-11T00:00:00+00:00'}), encoding='utf-8')
        (self.worker.directory / 'worker.log').write_text(
            'PermissionError: [WinError 5] Access is denied: status.json', encoding='utf-8')
        self.worker._close_log()  # Simulate the previous FastAPI process having exited.
        recovered = WebcamWorker(process_factory=self.worker.process_factory, state_dir=self.temp.name)
        with patch('backend.webcam_worker._pid_alive', return_value=False), \
                patch('backend.webcam_worker._pid_confirmed_gone', return_value=True):
            crashed = recovered.status()
            self.assertEqual(crashed['state'], 'error')
            self.assertIn('PermissionError: [WinError 5]', crashed['message'])
            restarted = recovered.start(python=sys.executable, event_origin='CWebcamPublisher',
                                        base_url='http://127.0.0.1:3001/TinyIoT')
            self.assertEqual(restarted['state'], 'starting')
        recovered.shutdown()

    def test_refresh_recovers_live_worker_and_stop_signal(self):
        self.start()
        (self.worker.directory / 'status.json').write_text(json.dumps({
            'state': 'running', 'pid': 1234,
            'updatedAt': '2099-11-11T00:00:00+00:00'}), encoding='utf-8')
        recovered = WebcamWorker(state_dir=self.temp.name)
        with patch('backend.webcam_worker._pid_alive', return_value=True):
            self.assertEqual(recovered.status()['state'], 'running')
            self.assertEqual(recovered.request_stop()['state'], 'stopping')
            self.assertTrue((recovered.directory / 'stop').exists())

    def test_worker_crash_is_visible_and_next_start_is_clean(self):
        self.start()
        (self.worker.directory / 'worker.log').write_text(
            'Traceback (most recent call last):\nPermissionError: [WinError 5] Access is denied: status.json\n',
            encoding='utf-8')
        self.processes[0].exit_code = 1
        crashed = self.worker.status()
        self.assertEqual(crashed['state'], 'error')
        self.assertIn('PermissionError: [WinError 5]', crashed['message'])
        self.assertEqual(self.start()['state'], 'starting')
        self.assertEqual(len(self.commands), 2)
        self.assertFalse((self.worker.directory / 'stop').exists())

    def test_journal_failure_is_visible_in_worker_status(self):
        self.start()
        (self.worker.directory / 'worker.log').write_text(
            'EventJournalError: Cannot persist repetition event\n', encoding='utf-8')
        self.processes[0].exit_code = 1
        crashed = self.worker.status()
        self.assertEqual(crashed['state'], 'error')
        self.assertIn('EventJournalError: Cannot persist', crashed['message'])

    def test_rejects_unverified_or_missing_publisher(self):
        for origin in (None, 'CAdmin'):
            with self.assertRaises(ValueError):
                self.worker.start(python=sys.executable, event_origin=origin,
                                  base_url='http://127.0.0.1:3001/TinyIoT')
        self.assertEqual(self.commands, [])


class WebcamRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_status_and_stop_use_one_worker(self):
        processes = []
        def launch(command, **kwargs):
            process = FakeProcess()
            processes.append(process)
            return process
        temp = tempfile.TemporaryDirectory()
        worker = WebcamWorker(process_factory=launch, state_dir=temp.name)
        transport = httpx.ASGITransport(app=main.app)
        try:
            with patch.object(main, 'webcam_worker', worker), patch.dict(os.environ, {
                    'CINDYSENSE_EVENT_ORIGIN': 'CWebcamPublisher',
                    'CINDYSENSE_SUMMARY_ORIGIN': 'CWebcamSummary',
                    'CINDYSENSE_WEBCAM_PYTHON': sys.executable}):
                async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
                    self.assertEqual((await client.post('/api/workout/webcam-worker/start')).status_code, 200)
                    self.assertEqual((await client.post('/api/workout/webcam-worker/start')).status_code, 200)
                    self.assertEqual((await client.get('/api/workout/webcam-worker')).json()['state'], 'starting')
                    self.assertEqual((await client.post('/api/workout/webcam-worker/stop')).json()['state'], 'stopping')
                    self.assertEqual((await client.get('/api/workout/webcam-worker')).json()['state'], 'stopping')
                    self.assertEqual(len(processes), 1)
        finally:
            worker.shutdown()
            if worker.directory:
                shutil.rmtree(worker.directory, ignore_errors=True)
            temp.cleanup()

    async def test_preview_stream_reads_worker_jpeg(self):
        process = FakeProcess()
        temp = tempfile.TemporaryDirectory()
        worker = WebcamWorker(process_factory=lambda command, **kwargs: process, state_dir=temp.name)
        worker.start(python=sys.executable, event_origin='CWebcamPublisher',
                     base_url='http://127.0.0.1:3001/TinyIoT')
        (worker.directory / 'status.json').write_text('{"state":"running"}', encoding='utf-8')
        (worker.directory / 'frame.jpg').write_bytes(b'\xff\xd8camera-frame\xff\xd9')

        class LocalRequest:
            client = type('Client', (), {'host': '127.0.0.1'})()

            async def is_disconnected(self):
                return False

        try:
            with patch.object(main, 'webcam_worker', worker):
                response = await main.webcam_worker_preview(LocalRequest())
                chunk = await anext(response.body_iterator)
                self.assertIn(b'Content-Type: image/jpeg', chunk)
                self.assertIn(b'camera-frame', chunk)
                await response.body_iterator.aclose()
        finally:
            worker.shutdown()
            shutil.rmtree(worker.directory, ignore_errors=True)
            temp.cleanup()
