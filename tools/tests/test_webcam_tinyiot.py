import json
import queue
import tempfile
import threading
import time
import uuid
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.webcam_tinyiot import DEFAULT_BASE, PosePublisher, TinyIoTClient, TinyIoTError


class EventUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TinyIoTClient()
        self.event = {
            "eventId": "a18a9ce4-684d-45bc-944e-81dc7442828c", "eventSequence": 1,
            "sessionId": "webcam-20261105T120000Z", "exercise": "pullup",
            "repetitions": 1, "round": 1, "timestamp": "2026-11-05T12:00:00+00:00",
            "source": "webcam",
        }

    def test_default_endpoint_uses_cindysense_port(self):
        self.assertEqual(DEFAULT_BASE, 'http://127.0.0.1:3001/TinyIoT')

    def test_posts_under_stable_idempotent_name(self):
        calls = []

        def request(method, suffix="", body=None, ty=None):
            calls.append((method, suffix, body, ty))
            return (404, None, {}) if method == "GET" else (201, "2001", {})

        self.client._request = request
        self.assertEqual(self.client.publish_event(self.event), "2001")
        self.assertEqual(calls[0][1], "/CindySense_AE/RepetitionCount/webcam-a18a9ce4684d45bc944e81dc7442828c")
        self.assertEqual(calls[1][3], 4)
        self.assertEqual(json.loads(calls[1][2]["m2m:cin"]["con"]), self.event)

    def test_localhost_uses_ipv4_without_changing_onem2m_path(self):
        response = MagicMock(status=404)
        response.read.return_value = b'{}'
        response.getheader.return_value = None
        with patch('tools.webcam_tinyiot.http.client.HTTPConnection') as connection:
            connection.return_value.getresponse.return_value = response
            client = TinyIoTClient('http://localhost:3000/TinyIoT', 'CAdmin')
            status, _, _ = client._request('GET', '/CindySense_AE/RepetitionCount/missing')
            self.assertEqual(status, 404)
            connection.assert_called_once_with('127.0.0.1', 3000, timeout=3.0)
            self.assertEqual(connection.return_value.request.call_args.args[:2],
                             ('GET', '/TinyIoT/CindySense_AE/RepetitionCount/missing'))

    def test_ambiguous_post_is_confirmed_by_exact_readback(self):
        resource_name = "webcam-" + self.event["eventId"].replace("-", "")
        cin = {"rn": resource_name, "con": json.dumps(self.event)}
        calls = iter([(404, None, {}), (500, None, {}), (200, None, {"m2m:cin": cin})])
        self.client._request = lambda *args, **kwargs: next(calls)
        self.assertEqual(self.client.publish_event(self.event), "verified")

    def test_mismatched_readback_is_not_confirmed(self):
        self.client._request = lambda *args, **kwargs: (200, None, {"m2m:cin": {"con": "{}"}})
        with self.assertRaises(TinyIoTError):
            self.client.publish_event(self.event)

    def test_event_offer_reports_bounded_queue_overflow(self):
        publisher = PosePublisher.__new__(PosePublisher)
        publisher.events = queue.Queue(maxsize=1)
        publisher.event_overflow = 0
        self.assertTrue(publisher.offer_event(self.event))
        self.assertFalse(publisher.offer_event({**self.event, "eventSequence": 2}))
        self.assertEqual(publisher.event_overflow, 1)
        self.assertEqual(publisher.events.qsize(), 1)

    def test_diagnostics_exposes_event_latency_without_live_threads(self):
        publisher = PosePublisher.__new__(PosePublisher)
        publisher.events = queue.Queue(maxsize=5)
        publisher.inflight_event = None
        publisher.event_confirmed = 1
        publisher.event_retries = 0
        publisher.event_overflow = 0
        publisher.event_last_latency_ms = 42.5
        publisher.event_max_latency_ms = 91.0
        diagnostics = publisher.diagnostics()
        self.assertEqual(diagnostics["eventLastLatencyMs"], 42.5)
        self.assertEqual(diagnostics["eventMaxLatencyMs"], 91.0)

    def test_shutdown_spools_offline_events_and_replays_same_id(self):
        recordings = Path(__file__).resolve().parents[2] / "data" / "recordings"
        recordings.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=recordings) as directory:
            spool = Path(directory) / "pending.json"
            with patch.object(TinyIoTClient, "publish_event", side_effect=TinyIoTError("offline")):
                publisher = PosePublisher(event_spool=spool, shutdown_grace=0.1)
                self.assertTrue(publisher.offer_event(self.event))
                publisher.close()
            self.assertEqual(json.loads(spool.read_text(encoding="utf-8")), [self.event])
            delivered = []
            def confirm(client, event, container):
                delivered.append(event)
                return "verified"
            with patch.object(TinyIoTClient, "publish_event", confirm):
                publisher = PosePublisher(event_spool=spool, shutdown_grace=1.0)
                publisher.close()
            self.assertEqual(delivered, [self.event])
            self.assertFalse(spool.exists())

    def test_slow_pose_upload_does_not_delay_repetition_worker(self):
        pose_started = threading.Event()
        release_pose = threading.Event()
        event_confirmed = threading.Event()

        def slow_pose(client, pose):
            pose_started.set()
            release_pose.wait(2)
            return '2001'

        def confirm(client, event, container):
            event_confirmed.set()
            return '2001'

        recordings = Path(__file__).resolve().parents[2] / 'data' / 'recordings'
        recordings.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=recordings) as directory:
            with (patch.object(TinyIoTClient, 'publish_pose', slow_pose),
                  patch.object(TinyIoTClient, 'publish_event', confirm)):
                publisher = PosePublisher(event_spool=Path(directory) / 'pending.json')
                try:
                    publisher.offer({'phase': 'DOWN', 'repCount': 0})
                    self.assertTrue(pose_started.wait(1))
                    started = time.monotonic()
                    self.assertTrue(publisher.offer_event(self.event))
                    self.assertTrue(event_confirmed.wait(0.5))
                    self.assertLess(time.monotonic() - started, 0.5)
                finally:
                    release_pose.set()
                    publisher.close()

    def test_thirty_event_burst_flushes_once_per_id(self):
        release = threading.Event()
        delivered = []

        def confirm(client, event, container):
            release.wait(2)
            delivered.append(event['eventId'])
            return '2001'

        recordings = Path(__file__).resolve().parents[2] / 'data' / 'recordings'
        recordings.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=recordings) as directory:
            with patch.object(TinyIoTClient, 'publish_event', confirm):
                publisher = PosePublisher(event_spool=Path(directory) / 'pending.json')
                try:
                    identifiers = [str(uuid.uuid4()) for _ in range(30)]
                    for count, identifier in enumerate(identifiers, 1):
                        self.assertTrue(publisher.offer_event({**self.event,
                            'eventId': identifier, 'eventSequence': count,
                            'repetitions': count}))
                    self.assertGreater(publisher.diagnostics()['eventQueueDepth'], 0)
                finally:
                    release.set()
                    publisher.close()
                self.assertEqual(delivered, identifiers)
                self.assertEqual(publisher.event_confirmed, 30)
                self.assertEqual(publisher.event_overflow, 0)


if __name__ == "__main__":
    unittest.main()
