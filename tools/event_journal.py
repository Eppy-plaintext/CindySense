"""Crash-safe, ordered local outbox for webcam repetition events."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID


class EventJournalError(RuntimeError):
    pass


def validate_event(event):
    """Validate the webcam subset of the structured repetition contract."""
    if not isinstance(event, dict) or set(event) != {
            'eventId', 'eventSequence', 'sessionId', 'exercise', 'repetitions',
            'round', 'timestamp', 'source'}:
        raise EventJournalError('Invalid repetition event fields')
    try:
        if not isinstance(event['timestamp'], str) or 'T' not in event['timestamp']:
            raise ValueError('Timestamp must be an ISO 8601 string')
        UUID(event['eventId'])
        timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError) as exc:
        raise EventJournalError('Invalid event identity or timestamp') from exc
    if (timestamp.utcoffset() is None or
            not isinstance(event['sessionId'], str) or not event['sessionId'] or
            len(event['sessionId']) > 100 or
            any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in event['sessionId']) or
            event['sessionId'][0] not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' or
            event['exercise'] not in ('pullup', 'pushup', 'squat') or
            event['source'] != 'webcam' or
            type(event['eventSequence']) is not int or event['eventSequence'] < 1 or
            type(event['repetitions']) is not int or event['repetitions'] < 0 or
            type(event['round']) is not int or event['round'] < 1):
        raise EventJournalError('Invalid structured webcam repetition event')
    return json.dumps(event, separators=(',', ':'), allow_nan=False)


class EventJournal:
    def __init__(self, path, capacity=20000):
        self.path = Path(path)
        self.capacity = capacity
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS events ('
                           'id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, '
                           'session_id TEXT NOT NULL, sequence INTEGER NOT NULL, '
                           'payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT "pending", '
                           'attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)')
                db.execute('CREATE UNIQUE INDEX IF NOT EXISTS event_session_sequence '
                           'ON events(session_id, sequence)')
                db.execute('CREATE TABLE IF NOT EXISTS summaries ('
                           'session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                           'event_count INTEGER NOT NULL, state TEXT NOT NULL DEFAULT "pending", '
                           'attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)')
        except (sqlite3.Error, OSError) as exc:
            raise EventJournalError('Cannot initialize repetition event journal') from exc

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            with connection:
                yield connection
        finally:
            connection.close()

    def append(self, event, session_id):
        payload = validate_event(event)
        if event['sessionId'] != session_id:
            raise EventJournalError('Repetition event belongs to a different workout session')
        try:
            with self._db() as db:
                existing = db.execute('SELECT payload FROM events WHERE event_id=?',
                                      (event['eventId'],)).fetchone()
                if existing:
                    if json.loads(existing[0]) != event:
                        raise EventJournalError('Conflicting duplicate repetition event')
                    return False
                if db.execute('SELECT 1 FROM summaries WHERE session_id=?',
                              (session_id,)).fetchone():
                    raise EventJournalError('Workout session is sealed; no more repetitions accepted')
                count = db.execute("SELECT count(*) FROM events WHERE state != 'delivered'").fetchone()[0]
                if count >= self.capacity:
                    raise EventJournalError('Repetition event journal is full')
                last_sequence = db.execute('SELECT max(sequence) FROM events WHERE session_id=?',
                                           (session_id,)).fetchone()[0]
                if event['eventSequence'] != (last_sequence or 0) + 1:
                    raise EventJournalError('Non-contiguous repetition event sequence')
                prior = db.execute('SELECT payload FROM events WHERE session_id=? AND '
                                   'json_extract(payload, "$.exercise")=? ORDER BY sequence DESC LIMIT 1',
                                   (session_id, event['exercise'])).fetchone()
                prior_count = json.loads(prior[0])['repetitions'] if prior else 0
                if event['repetitions'] != prior_count + 1:
                    raise EventJournalError('Non-contiguous cumulative repetition count')
                db.execute('INSERT INTO events(event_id,session_id,sequence,payload) VALUES(?,?,?,?)',
                           (event['eventId'], session_id, event['eventSequence'], payload))
            return True
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot persist repetition event') from exc

    def head(self):
        try:
            with self._db() as db:
                row = db.execute("SELECT id,payload,state FROM events WHERE state != 'delivered' "
                                 'ORDER BY id LIMIT 1').fetchone()
            return (row[0], json.loads(row[1]), row[2]) if row else None
        except (sqlite3.Error, ValueError) as exc:
            raise EventJournalError('Cannot read repetition event journal') from exc

    def acknowledge(self, row_id):
        self._transition(row_id, 'delivered', None)

    def attempt(self, row_id, error, permanent=False):
        self._transition(row_id, 'failed' if permanent else 'pending', error, increment=True)

    def _transition(self, row_id, state, error, increment=False):
        try:
            with self._db() as db:
                cursor = db.execute('UPDATE events SET state=?, last_error=?, attempts=attempts+? '
                                    'WHERE id=? AND state="pending"',
                                    (state, error, int(increment), row_id))
                if cursor.rowcount != 1:
                    raise EventJournalError('Repetition event acknowledgement state changed')
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot update repetition event journal') from exc

    def metrics(self):
        try:
            with self._db() as db:
                rows = db.execute('SELECT state,count(*),coalesce(sum(attempts),0) '
                                  'FROM events GROUP BY state').fetchall()
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot read repetition event metrics') from exc
        result = {'pending': 0, 'delivered': 0, 'failed': 0, 'retried': 0}
        for state, count, attempts in rows:
            result[state] = count
            result['retried'] += attempts
        return result

    def queue_summary(self, summary, event_count):
        """Atomically seal a session after checking its full local event ledger."""
        if (not isinstance(summary, dict) or not isinstance(summary.get('sessionId'), str) or
                summary.get('source') != 'webcam' or summary.get('status') != 'completed' or
                summary.get('completionReason') not in ('timer', 'explicit_stop') or
                type(event_count) is not int or event_count < 0):
            raise EventJournalError('Invalid completed workout summary')
        session_id = summary['sessionId']
        try:
            payload = json.dumps(summary, separators=(',', ':'), allow_nan=False)
            with self._db() as db:
                existing = db.execute('SELECT payload,event_count FROM summaries WHERE session_id=?',
                                      (session_id,)).fetchone()
                if existing:
                    if json.loads(existing[0]) != summary or existing[1] != event_count:
                        raise EventJournalError('Conflicting completed workout summary')
                    return False
                rows = db.execute('SELECT sequence,payload FROM events WHERE session_id=? ORDER BY sequence',
                                  (session_id,)).fetchall()
                if [row[0] for row in rows] != list(range(1, event_count + 1)):
                    raise EventJournalError('Session repetition ledger is incomplete')
                totals = {'pullup': 0, 'pushup': 0, 'squat': 0}
                for _, stored in rows:
                    item = json.loads(stored)
                    totals[item['exercise']] = max(totals[item['exercise']], item['repetitions'])
                if any(summary.get(name) != totals[exercise] for exercise, name in
                       (('pullup', 'pullups'), ('pushup', 'pushups'), ('squat', 'squats'))):
                    raise EventJournalError('Summary counts do not match repetition ledger')
                db.execute('INSERT INTO summaries(session_id,payload,event_count) VALUES(?,?,?)',
                           (session_id, payload, event_count))
            return True
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise EventJournalError('Cannot persist completed workout summary') from exc

    def ready_summary(self):
        """Return oldest summary only when every event for that session is confirmed."""
        try:
            with self._db() as db:
                row = db.execute('SELECT session_id,payload,event_count,state FROM summaries '
                                 'WHERE state != "delivered" ORDER BY rowid LIMIT 1').fetchone()
                if not row:
                    return None
                remaining = db.execute('SELECT count(*) FROM events WHERE session_id=? '
                                       'AND state != "delivered"', (row[0],)).fetchone()[0]
                if remaining:
                    return None
                return row[0], json.loads(row[1]), row[3]
        except (sqlite3.Error, ValueError) as exc:
            raise EventJournalError('Cannot inspect pending workout summary') from exc

    def summary_status(self, session_id):
        try:
            with self._db() as db:
                row = db.execute('SELECT state FROM summaries WHERE session_id=?',
                                 (session_id,)).fetchone()
            return row[0] if row else None
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot read workout summary status') from exc

    def update_summary(self, session_id, state, error=None):
        if state not in ('pending', 'delivered', 'failed'):
            raise ValueError('Invalid summary state')
        try:
            with self._db() as db:
                cursor = db.execute('UPDATE summaries SET state=?,last_error=?,attempts=attempts+? '
                                    'WHERE session_id=? AND state="pending"',
                                    (state, error, int(error is not None), session_id))
                if cursor.rowcount != 1:
                    raise EventJournalError('Workout summary state changed')
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot update workout summary status') from exc

    def summary_metrics(self):
        try:
            with self._db() as db:
                rows = db.execute('SELECT state,count(*),coalesce(sum(attempts),0) '
                                  'FROM summaries GROUP BY state').fetchall()
        except sqlite3.Error as exc:
            raise EventJournalError('Cannot read workout summary metrics') from exc
        result = {'pending': 0, 'delivered': 0, 'failed': 0, 'retried': 0}
        for state, count, attempts in rows:
            result[state] = count
            result['retried'] += attempts
        return result
