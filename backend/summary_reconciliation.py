"""Opt-in read-only scan for webcam summaries whose callback was missed."""

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from .cindypt import WorkoutSummary
from .tinyiot import TinyIoTError

log = logging.getLogger(__name__)


class SummaryReconciler:
    def __init__(self, jobs, enabled=False, interval_seconds=60, max_pages=5):
        self.jobs = jobs
        self.enabled = enabled
        self.interval_seconds = max(5, interval_seconds)
        self.max_pages = max_pages
        self.state = {'enabled': enabled, 'lastCheckAt': None, 'lastError': None,
                      'scanned': 0, 'enqueued': 0, 'duplicates': 0, 'invalid': 0}
        if enabled:
            with jobs._db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS reconciliation_state ('
                           'name TEXT PRIMARY KEY, last_ref TEXT NOT NULL)')

    def metrics(self):
        return dict(self.state)

    def _checkpoint(self, resource):
        with self.jobs._db() as db:
            row = db.execute('SELECT last_ref FROM reconciliation_state WHERE name=?',
                             (resource,)).fetchone()
        return row[0] if row else None

    def _save_checkpoint(self, resource, ref):
        with self.jobs._db() as db:
            db.execute('INSERT INTO reconciliation_state(name,last_ref) VALUES(?,?) '
                       'ON CONFLICT(name) DO UPDATE SET last_ref=excluded.last_ref',
                       (resource, ref))

    @staticmethod
    def _valid_summary(ref, content, instance):
        name = ref.rsplit('/', 1)[-1]
        if (not name.startswith('webcam-summary-webcam-') or
                not isinstance(instance, dict) or
                instance.get('rn') != name or
                not isinstance(instance.get('ri'), str) or not instance['ri'] or
                not isinstance(content, dict) or content.get('source') != 'webcam' or
                content.get('synthetic') is True or
                content.get('status') != 'completed' or
                content.get('completionReason') not in ('timer', 'explicit_stop')):
            return False
        try:
            summary = WorkoutSummary.model_validate(content)
        except ValidationError:
            return False
        return (bool(summary.sessionId) and name == 'webcam-summary-' + summary.sessionId and
                summary.workoutId in (None, summary.sessionId) and
                all(key in content for key in ('pullups', 'pushups', 'squats')))

    async def check_once(self, client, settings):
        self.state['lastCheckAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        if not self.enabled or not settings.cindypt_origin:
            return self.metrics()
        resource = settings.workout_summary_resource.strip('/')
        try:
            checkpoint = self._checkpoint(resource)
            parent = await client.retrieve(resource, settings.cindypt_origin)
            if 'm2m:cnt' not in parent:
                raise TinyIoTError('invalid_resource', 'Summary resource is not a container.', 502)
            refs = await client.list_content_instances(
                resource, settings.cindypt_origin, max_pages=self.max_pages,
                stop_at={checkpoint} if checkpoint else None,
                expected_count=parent['m2m:cnt'].get('cni'))
            for ref in reversed(refs):
                if ref == checkpoint:
                    continue
                content, instance = client.parse_content_instance(
                    await client.retrieve(ref, settings.cindypt_origin))
                self.state['scanned'] += 1
                if not self._valid_summary(ref, content, instance):
                    self.state['invalid'] += 1
                    continue
                if self.jobs.enqueue(ref, instance['ri']):
                    self.state['enqueued'] += 1
                else:
                    self.state['duplicates'] += 1
            if refs and refs[0] != checkpoint:
                self._save_checkpoint(resource, refs[0])
            self.state['lastError'] = None
        except TinyIoTError as exc:
            self.state['lastError'] = exc.code
            log.warning('Summary reconciliation deferred: %s', exc.code)
        except sqlite3.Error:
            self.state['lastError'] = 'local_database_error'
            log.error('Summary reconciliation deferred: local database error')
        return self.metrics()

    async def run(self, client, settings):
        while True:
            await self.check_once(client, settings)
            await asyncio.sleep(self.interval_seconds)
