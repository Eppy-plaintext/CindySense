"""Stage B2 summary barrier tests; no live TinyIoT or camera."""

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from tools.event_journal import EventJournal, EventJournalError
from tools.webcam_tinyiot import PosePublisher, TinyIoTClient, TinyIoTConnectionError
from tools.workout_summary import SummaryPublisher, completed_summary

SESSION = 'webcam-20261118T000000Z-12345678'
OTHER = 'webcam-20261118T000100Z-87654321'


def event(session=SESSION):
    return {'eventId': str(uuid4()), 'eventSequence': 1, 'sessionId': session,
            'exercise': 'pullup', 'repetitions': 1, 'round': 1,
            'timestamp': '2026-11-18T00:00:00+00:00', 'source': 'webcam'}


def summary(session=SESSION):
    return completed_summary(session, SimpleNamespace(rounds=0, elapsed=60),
                             {'pullup': 1, 'pushup': 0, 'squat': 0}, 'explicit_stop')


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class BarrierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spool = Path(self.temp.name) / 'legacy.json'

    def test_summary_waits_for_readback_confirmed_event_and_seals_session(self):
        release = threading.Event()
        started = threading.Event()
        def deliver(client, item, container):
            started.set()
            release.wait(2)
            return 'verified'
        with patch.object(TinyIoTClient, 'publish_event', deliver):
            publisher = PosePublisher(event_spool=self.spool, session_id=SESSION)
            summaries = Mock()
            summaries.publish.return_value = 'created'
            publisher.set_summary_publisher(summaries)
            try:
                self.assertTrue(publisher.offer_event(event()))
                self.assertTrue(started.wait(1))
                self.assertEqual(publisher.complete_summary(summary(), 1, timeout=0.05), 'pending')
                summaries.publish.assert_not_called()
                with self.assertRaisesRegex(EventJournalError, 'sealed'):
                    publisher.offer_event({**event(), 'eventSequence': 2})
                release.set()
                self.assertTrue(wait_until(lambda: publisher.journal.summary_status(SESSION) == 'delivered'))
                summaries.publish.assert_called_once()
            finally:
                release.set()
                publisher.close()

    def test_outage_keeps_events_and_summary_pending_across_restart(self):
        with patch.object(TinyIoTClient, 'publish_event',
                          side_effect=TinyIoTConnectionError('offline')):
            publisher = PosePublisher(event_spool=self.spool, session_id=SESSION,
                                      shutdown_grace=0.01)
            publisher.set_summary_publisher(Mock())
            publisher.offer_event(event())
            self.assertEqual(publisher.complete_summary(summary(), 1, timeout=0.05), 'pending')
            publisher.close()
        self.assertEqual(publisher.journal.metrics()['pending'], 1)
        self.assertEqual(publisher.journal.summary_metrics()['pending'], 1)
        with patch.object(TinyIoTClient, 'publish_event', return_value='verified'):
            restarted = PosePublisher(event_spool=self.spool, session_id=OTHER)
            summaries = Mock()
            summaries.publish.return_value = 'verified'
            restarted.set_summary_publisher(summaries)
            try:
                self.assertTrue(wait_until(lambda: restarted.journal.summary_status(SESSION) == 'delivered'))
                self.assertEqual(restarted.journal.metrics()['delivered'], 1)
                summaries.publish.assert_called_once()
            finally:
                restarted.close()

    def test_duplicate_summary_and_cross_session_isolation(self):
        journal = EventJournal(self.spool.with_suffix('.sqlite3'))
        journal.append(event(), SESSION)
        self.assertTrue(journal.queue_summary(summary(), 1))
        self.assertFalse(journal.queue_summary(summary(), 1))
        with self.assertRaisesRegex(EventJournalError, 'Conflicting'):
            journal.queue_summary({**summary(), 'durationSeconds': 61}, 1)
        journal.append(event(OTHER), OTHER)
        self.assertIsNone(journal.ready_summary())
        first = journal.head()
        journal.acknowledge(first[0])
        self.assertEqual(journal.ready_summary()[0], SESSION)

    def test_incomplete_ledger_and_mismatched_totals_never_publish(self):
        journal = EventJournal(self.spool.with_suffix('.sqlite3'))
        journal.append(event(), SESSION)
        with self.assertRaisesRegex(EventJournalError, 'incomplete'):
            journal.queue_summary(summary(), 2)
        with self.assertRaisesRegex(EventJournalError, 'counts'):
            journal.queue_summary({**summary(), 'pullups': 2}, 1)
        self.assertEqual(journal.summary_metrics()['pending'], 0)

    def test_summary_sqlite_failure_is_visible_and_not_delivered(self):
        journal = EventJournal(self.spool.with_suffix('.sqlite3'))
        journal.append(event(), SESSION)
        with patch.object(journal, '_db', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(EventJournalError):
                journal.queue_summary(summary(), 1)
        self.assertIsNone(journal.summary_status(SESSION))

    def test_summary_outage_uses_bounded_attempts_and_remains_pending(self):
        with patch.object(TinyIoTClient, 'publish_event', return_value='verified'):
            publisher = PosePublisher(event_spool=self.spool, session_id=SESSION,
                                      shutdown_grace=0.01)
            publisher.offer_event(event())
            self.assertTrue(wait_until(lambda: publisher.journal.metrics()['delivered'] == 1))
            client = Mock()
            client.publish_summary.side_effect = TinyIoTConnectionError('offline')
            publisher.set_summary_publisher(SummaryPublisher(client, attempts=2, retry_delay=0))
            status = publisher.complete_summary(summary(), 1, timeout=0.1)
            publisher.close()
        self.assertEqual(status, 'pending')
        self.assertEqual(client.publish_summary.call_count, 2)
        self.assertEqual(publisher.journal.summary_metrics()['pending'], 1)
        self.assertGreaterEqual(publisher.journal.summary_metrics()['retried'], 1)


if __name__ == '__main__':
    unittest.main()
