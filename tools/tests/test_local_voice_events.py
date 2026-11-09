import json
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
