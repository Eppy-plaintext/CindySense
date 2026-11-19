import json
import http.client
import queue
import threading
import unittest

from tools.local_voice_events import LocalVoiceEvents


class LocalVoiceEventsTests(unittest.TestCase):
    def test_bounded_stream_and_session_lifecycle(self):
        stream = LocalVoiceEvents.__new__(LocalVoiceEvents)
        stream.clients = {queue.Queue(maxsize=32)}
        stream.lock = threading.Lock()
        stream.current_session = None
        stream.current_session_delivered = False
        stream.emit({'type': 'start', 'sessionId': 'one'})
        self.assertEqual(stream.current_session['sessionId'], 'one')
        for number in range(40):
            stream.emit({'type': 'rep', 'eventId': str(number)})
        items = list(stream.clients)[0]
        self.assertEqual(items.qsize(), 32)
        first = json.loads(items.get())
        self.assertEqual(first['eventId'], '8')
        self.assertIsInstance(first['publishedAtEpochMs'], int)
        stream.emit({'type': 'reset', 'sessionId': 'one'})
        self.assertIsNone(stream.current_session)

    def test_only_claimed_client_receives_workout_events(self):
        stream = LocalVoiceEvents.__new__(LocalVoiceEvents)
        stream.clients = set()
        stream.lock = threading.Lock()
        stream.available = threading.Condition(stream.lock)
        stream.current_session = None
        stream.current_session_delivered = False
        first, second = queue.Queue(), queue.Queue()

        with stream.available:
            self.assertEqual(stream.claim(first), (True, None))
            self.assertEqual(stream.claim(second), (False, None))
        stream.emit({'type': 'start', 'sessionId': 'one'})
        stream.emit({'type': 'rep', 'sessionId': 'one', 'eventId': 'rep-1'})
        self.assertEqual(first.qsize(), 2)
        self.assertTrue(second.empty())

        with stream.available:
            stream.clients.discard(first)
            self.assertEqual(stream.claim(second), (True, {'type': 'start', 'sessionId': 'one', 'replay': True}))
        stream.emit({'type': 'rep', 'sessionId': 'one', 'eventId': 'rep-2'})
        self.assertEqual(json.loads(second.get())['eventId'], 'rep-2')
        stream.emit({'type': 'reset', 'sessionId': 'one'})
        stream.emit({'type': 'start', 'sessionId': 'two'})
        self.assertEqual(stream.current_session['sessionId'], 'two')

    def test_first_listener_receives_start_when_worker_started_before_dashboard_connected(self):
        stream = LocalVoiceEvents.__new__(LocalVoiceEvents)
        stream.clients = set()
        stream.lock = threading.Lock()
        stream.current_session = None
        stream.current_session_delivered = False
        stream.emit({'type': 'start', 'sessionId': 'one'})
        with stream.lock:
            self.assertEqual(stream.claim(queue.Queue()),
                             (True, {'type': 'start', 'sessionId': 'one', 'replay': False}))

    def test_two_http_streams_have_one_speaker(self):
        stream = LocalVoiceEvents(port=0)
        port = stream.server.server_address[1]
        first = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        second = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            first.request('GET', '/events')
            first_response = first.getresponse()
            self.assertEqual(first_response.status, 200)
            self.assertEqual(first_response.readline(), b': connected\n')
            self.assertEqual(first_response.readline(), b'\n')
            self.assertEqual(first_response.readline(), b'event: speaker\n')
            self.assertEqual(first_response.readline(), b'data: active\n')
            self.assertEqual(first_response.readline(), b'\n')

            second.request('GET', '/events')
            second_response = second.getresponse()
            self.assertEqual(second_response.status, 200)
            self.assertEqual(second_response.readline(), b': connected\n')
            self.assertEqual(second_response.readline(), b'\n')
            stream.emit({'type': 'start', 'sessionId': 'one'})
            self.assertEqual(json.loads(first_response.readline()[6:])['sessionId'], 'one')
            with stream.lock:
                self.assertEqual(len(stream.clients), 1)
        finally:
            first.close()
            second.close()
            stream.close()
