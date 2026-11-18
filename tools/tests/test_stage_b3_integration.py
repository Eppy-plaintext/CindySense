"""Disposable CSE failure tests for the B1/B2 worker and completion barrier."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tools.event_journal import EventJournalError
from tools.webcam_tinyiot import PosePublisher, TinyIoTClient, TinyIoTConnectionError
from tools.workout_summary import SummaryPublisher, completed_summary


FIRST = 'webcam-20261118T000000Z-a1b2c3d4'
SECOND = 'webcam-20261118T000100Z-b1c2d3e4'


def event(session, sequence):
    return {'eventId': str(uuid4()), 'eventSequence': sequence, 'sessionId': session,
            'exercise': 'pullup', 'repetitions': sequence, 'round': 1,
            'timestamp': '2026-11-18T00:00:00+00:00', 'source': 'webcam'}


def summary(session, count):
    return completed_summary(session, SimpleNamespace(rounds=0, elapsed=60),
                             {'pullup': count, 'pushup': 0, 'squat': 0}, 'explicit_stop')


def wait_for(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class DisposableCSE:
    def __init__(self):
        self.content = {}
        self.posts = {}
        self.online = True
        self.lost_reply_once = False
        self.summary_offline = False

    def request(self, client, method, suffix='', body=None, ty=None):
        if not self.online or (self.summary_offline and 'WorkoutSummary' in suffix):
            raise TinyIoTConnectionError('disposable CSE offline')
        if method == 'GET' and suffix in self.content:
            return 200, '2000', {'m2m:cin': self.content[suffix]}
        if method == 'GET' and suffix.endswith('/WorkoutSummary'):
            return 200, '2000', {'m2m:cnt': {}}
        if method == 'GET':
            return 404, '4004', {}
        assert method == 'POST' and ty == 4
        cin = body['m2m:cin']
        path = suffix + '/' + cin['rn']
        if path in self.content:
            return 409, '4105', {}
        self.content[path] = {'rn': cin['rn'], 'con': cin['con']}
        self.posts[path] = self.posts.get(path, 0) + 1
        if self.lost_reply_once:
            self.lost_reply_once = False
            return 503, '5000', {}
        return 201, '2001', {}


class StageB3WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spool = Path(self.temp.name) / 'legacy.json'
        self.cse = DisposableCSE()
        def request(client, method, suffix='', body=None, ty=None):
            return self.cse.request(client, method, suffix, body, ty)
        self.patch_request = patch.object(TinyIoTClient, '_request', request)
        self.patch_request.start()
        self.addCleanup(self.patch_request.stop)

    def publisher(self, session):
        publisher = PosePublisher(event_spool=self.spool, session_id=session, shutdown_grace=0.01)
        publisher.set_summary_publisher(SummaryPublisher(TinyIoTClient(), attempts=1))
        return publisher

    def test_outage_crash_restart_and_multiple_pending_sessions(self):
        self.cse.online = False
        first = self.publisher(FIRST)
        first.offer_event(event(FIRST, 1))
        self.assertEqual(first.complete_summary(summary(FIRST, 1), 1, timeout=0.05), 'pending')
        first.close()
        second = self.publisher(SECOND)
        second.offer_event(event(SECOND, 1))
        self.assertEqual(second.complete_summary(summary(SECOND, 1), 1, timeout=0.05), 'pending')
        second.close()
        self.assertEqual(second.diagnostics()['summaryPending'], 2)
        self.assertEqual(second.diagnostics()['eventJournalPending'], 2)
        self.cse.online = True
        restarted = self.publisher('new-session')
        try:
            self.assertTrue(wait_for(lambda: restarted.diagnostics()['summaryDelivered'] == 2))
            self.assertEqual(restarted.diagnostics()['eventJournalDelivered'], 2)
            self.assertEqual(restarted.diagnostics()['summaryPending'], 0)
            self.assertEqual(len(self.cse.posts), 4)
        finally:
            restarted.close()
        replay = self.publisher('another-session')
        try:
            self.assertEqual(replay.diagnostics()['summaryDelivered'], 2)
            self.assertEqual(len(self.cse.posts), 4)
        finally:
            replay.close()

    def test_lost_event_response_reads_back_once_before_summary(self):
        self.cse.lost_reply_once = True
        publisher = self.publisher(FIRST)
        publisher.offer_event(event(FIRST, 1))
        try:
            self.assertEqual(publisher.complete_summary(summary(FIRST, 1), 1, timeout=3), 'delivered')
            self.assertEqual(publisher.diagnostics()['eventJournalDelivered'], 1)
            self.assertEqual(len(self.cse.posts), 2)
        finally:
            publisher.close()

    def test_summary_outage_keeps_verified_event_and_recovers_on_restart(self):
        self.cse.summary_offline = True
        publisher = self.publisher(FIRST)
        publisher.offer_event(event(FIRST, 1))
        self.assertTrue(wait_for(lambda: publisher.diagnostics()['eventJournalDelivered'] == 1))
        self.assertEqual(publisher.complete_summary(summary(FIRST, 1), 1, timeout=0.1), 'pending')
        publisher.close()
        self.assertEqual(publisher.diagnostics()['eventJournalPending'], 0)
        self.assertEqual(publisher.diagnostics()['summaryPending'], 1)
        self.cse.summary_offline = False
        restarted = self.publisher(SECOND)
        try:
            self.assertTrue(wait_for(lambda: restarted.diagnostics()['summaryDelivered'] == 1))
            self.assertEqual(len(self.cse.posts), 2)
        finally:
            restarted.close()

    def test_acknowledgement_disk_failure_replays_without_second_remote_post(self):
        publisher = self.publisher(FIRST)
        with patch.object(publisher.journal, 'acknowledge',
                          side_effect=EventJournalError('disk full')):
            publisher.offer_event(event(FIRST, 1))
            self.assertTrue(wait_for(lambda: publisher.fatal_error == 'journal_error'))
            self.assertEqual(publisher.diagnostics()['eventJournalPending'], 1)
        publisher.close()
        self.assertEqual(len(self.cse.posts), 1)
        restarted = self.publisher(SECOND)
        try:
            self.assertTrue(wait_for(lambda: restarted.diagnostics()['eventJournalDelivered'] == 1))
            self.assertEqual(len(self.cse.posts), 1)
        finally:
            restarted.close()


if __name__ == '__main__':
    unittest.main()
