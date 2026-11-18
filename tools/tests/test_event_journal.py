"""Stage B1 outbox tests; all TinyIoT interactions are mocked."""

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tools.event_journal import EventJournal, EventJournalError
from tools.webcam_tinyiot import (PosePublisher, TinyIoTClient,
                                  TinyIoTConnectionError, RetryableEventError)


SESSION = 'webcam-20261118T000000Z-test'


def event(number=1, session=SESSION, identifier=None):
    return {'eventId': identifier or str(uuid4()), 'eventSequence': number,
            'sessionId': session, 'exercise': 'pullup', 'repetitions': number,
            'round': 1, 'timestamp': '2026-11-18T00:00:00+00:00',
            'source': 'webcam'}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pending.sqlite3'
        self.journal = EventJournal(self.path)

    def test_normal_persistence_and_acknowledgement(self):
        item = event()
        self.assertTrue(self.journal.append(item, SESSION))
        row_id, stored, state = self.journal.head()
        self.assertEqual((stored, state), (item, 'pending'))
        self.journal.acknowledge(row_id)
        self.assertIsNone(self.journal.head())
        self.assertEqual(self.journal.metrics()['delivered'], 1)

    def test_temporary_failure_retains_event_and_counts_retry(self):
        self.journal.append(event(), SESSION)
        row_id, _, _ = self.journal.head()
        self.journal.attempt(row_id, 'TinyIoTConnectionError')
        self.assertEqual(self.journal.metrics()['retried'], 1)
        self.assertEqual(self.journal.head()[2], 'pending')

    def test_process_termination_simulation_reopens_pending_event(self):
        item = event()
        self.journal.append(item, SESSION)
        reopened = EventJournal(self.path)
        self.assertEqual(reopened.head()[1], item)

    def test_duplicate_identity_is_idempotent_but_conflict_is_rejected(self):
        item = event()
        self.assertTrue(self.journal.append(item, SESSION))
        self.assertFalse(self.journal.append(dict(item), SESSION))
        with self.assertRaises(EventJournalError):
            self.journal.append({**item, 'repetitions': 2}, SESSION)
        self.assertEqual(self.journal.metrics()['pending'], 1)

    def test_chronological_sequence_is_preserved(self):
        items = [event(number) for number in (1, 2, 3)]
        for item in items:
            self.journal.append(item, SESSION)
        observed = []
        while head := self.journal.head():
            observed.append(head[1]['eventSequence'])
            self.journal.acknowledge(head[0])
        self.assertEqual(observed, [1, 2, 3])

    def test_out_of_order_offer_cannot_reorder_session(self):
        with self.assertRaisesRegex(EventJournalError, 'Non-contiguous repetition event sequence'):
            self.journal.append(event(2), SESSION)
        self.journal.append(event(1), SESSION)
        with self.assertRaisesRegex(EventJournalError, 'Non-contiguous repetition event sequence'):
            self.journal.append(event(3), SESSION)
        self.assertEqual(self.journal.head()[1]['eventSequence'], 1)

    def test_skipped_or_regressed_cumulative_count_is_rejected_before_delivery(self):
        self.journal.append(event(1), SESSION)
        for count in (1, 3):
            with self.assertRaisesRegex(EventJournalError, 'Non-contiguous cumulative'):
                self.journal.append({**event(2), 'repetitions': count}, SESSION)
        self.assertEqual(self.journal.metrics()['pending'], 1)

    def test_sqlite_write_failure_is_not_reported_as_queued(self):
        with patch.object(self.journal, '_db', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(EventJournalError):
                self.journal.append(event(), SESSION)
        self.assertEqual(self.journal.metrics()['pending'], 0)

    def test_corrupt_database_fails_closed_on_restart(self):
        self.path.write_bytes(b'not a sqlite database')
        with self.assertRaisesRegex(EventJournalError, 'Cannot initialize'):
            EventJournal(self.path)

    def test_capacity_overflow_rejects_without_discarding_prior_event(self):
        journal = EventJournal(self.path, capacity=1)
        first = event()
        journal.append(first, SESSION)
        with self.assertRaisesRegex(EventJournalError, 'full'):
            journal.append(event(2), SESSION)
        self.assertEqual(journal.head()[1], first)

    def test_wrong_session_and_sequence_collision_are_rejected(self):
        with self.assertRaisesRegex(EventJournalError, 'different workout'):
            self.journal.append(event(), 'another-session')
        self.journal.append(event(), SESSION)
        with self.assertRaises(EventJournalError):
            self.journal.append(event(), SESSION)
        self.assertEqual(self.journal.metrics()['pending'], 1)

    def test_failed_head_blocks_later_events(self):
        self.journal.append(event(1), SESSION)
        self.journal.append(event(2), SESSION)
        row_id, _, _ = self.journal.head()
        self.journal.attempt(row_id, 'permission_denied', permanent=True)
        self.assertEqual(self.journal.head()[2], 'failed')
        self.assertEqual(self.journal.metrics()['failed'], 1)


class PublisherRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spool = Path(self.temp.name) / 'legacy.json'

    def test_repeated_restart_replays_same_id_once(self):
        item = event()
        journal = EventJournal(self.spool.with_suffix('.sqlite3'))
        journal.append(item, SESSION)
        for _ in range(2):
            with patch.object(TinyIoTClient, 'publish_event',
                              side_effect=TinyIoTConnectionError('offline')):
                publisher = PosePublisher(event_spool=self.spool, session_id='fresh-session',
                                          shutdown_grace=0.01)
                publisher.close()
                self.assertEqual(publisher.journal.metrics()['pending'], 1)
        delivered = []
        def confirm(client, content, container):
            delivered.append(content['eventId'])
            return 'verified'
        with patch.object(TinyIoTClient, 'publish_event', confirm):
            publisher = PosePublisher(event_spool=self.spool, session_id='fresh-session',
                                      shutdown_grace=1)
            publisher.close()
        self.assertEqual(delivered, [item['eventId']])
        self.assertEqual(publisher.journal.metrics()['delivered'], 1)

    def test_legacy_spool_imports_without_duplicate_republication(self):
        item = event()
        self.spool.write_text(json.dumps([item]), encoding='utf-8')
        delivered = []
        def confirm(client, content, container):
            delivered.append(content['eventId'])
            return 'verified'
        with patch.object(TinyIoTClient, 'publish_event', confirm):
            publisher = PosePublisher(event_spool=self.spool, session_id='fresh-session')
            publisher.close()
        self.assertFalse(self.spool.exists())
        self.assertEqual(delivered, [item['eventId']])

    def test_post_success_requires_exact_readback_before_ack(self):
        item = event()
        client = TinyIoTClient()
        calls = iter([(404, None, {}), (201, '2001', {}), (404, None, {})])
        client._request = lambda *args, **kwargs: next(calls)
        with self.assertRaises(RetryableEventError):
            client.publish_event(item)
        name = 'webcam-' + item['eventId'].replace('-', '')
        calls = iter([(404, None, {}), (201, '2001', {}),
                      (200, '2000', {'m2m:cin': {'rn': name, 'con': json.dumps(item)}})])
        client._request = lambda *args, **kwargs: next(calls)
        self.assertEqual(client.publish_event(item), '2001')

    def test_worker_acknowledges_only_after_verified_tinyiot_readback(self):
        item = event()
        remote = {}
        observed_pending_before_post = []
        journal_path = self.spool.with_suffix('.sqlite3')

        def request(client, method, suffix='', body=None, ty=None):
            if method == 'POST':
                observed_pending_before_post.append(EventJournal(journal_path).head()[1])
                remote['cin'] = {'rn': body['m2m:cin']['rn'],
                                 'con': body['m2m:cin']['con']}
                return 201, '2001', {}
            if remote:
                return 200, '2000', {'m2m:cin': remote['cin']}
            return 404, '4004', {}

        with patch.object(TinyIoTClient, '_request', request):
            publisher = PosePublisher(event_spool=self.spool, session_id=SESSION)
            try:
                self.assertTrue(publisher.offer_event(item))
                deadline = time.monotonic() + 2
                while publisher.journal.metrics()['delivered'] != 1 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(publisher.journal.metrics()['delivered'], 1)
            finally:
                publisher.close()
        self.assertEqual(observed_pending_before_post, [item])


if __name__ == '__main__':
    unittest.main()
