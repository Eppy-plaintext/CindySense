"""Durable, bounded-work notification queue for rule-based CindyPT advice."""

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from .cindypt import Goal, WorkoutSummary, analyze, recommend
from .fatigue_history import read_fatigue_history
from .workout_history import build_history
from .tinyiot import TinyIoTError

log = logging.getLogger(__name__)


class CoachingJobs:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs ('
                       'identity TEXT PRIMARY KEY, resource TEXT NOT NULL, '
                       'attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, '
                       'state TEXT NOT NULL DEFAULT \'pending\')')

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, resource: str, identity: str):
        with self._db() as db:
            result = db.execute('INSERT OR IGNORE INTO jobs(identity, resource, due) VALUES (?, ?, ?)',
                                (identity, resource, time.time()))
            return result.rowcount == 1

    def due(self):
        with self._db() as db:
            return db.execute("SELECT identity, resource, attempts FROM jobs "
                              "WHERE state='pending' AND due<=? ORDER BY due LIMIT 1",
                              (time.time(),)).fetchone()

    def finish(self, identity: str):
        with self._db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE identity=?", (identity,))

    def fail(self, identity: str):
        with self._db() as db:
            db.execute("UPDATE jobs SET state='error' WHERE identity=?", (identity,))

    def retry(self, identity: str, attempts: int):
        # Persist across restarts; capped backoff makes temporary failures recoverable.
        delay = min(300, 2 ** min(attempts, 8))
        with self._db() as db:
            db.execute("UPDATE jobs SET attempts=?, due=? WHERE identity=?",
                       (attempts, time.time() + delay, identity))

    def metrics(self):
        with self._db() as db:
            rows = db.execute('SELECT state, count(*) FROM jobs GROUP BY state').fetchall()
        return dict(rows)

    async def process_one(self, client, settings, on_saved=None):
        job = self.due()
        if job is None:
            return False
        identity, resource, attempts = job
        try:
            content, instance = client.parse_content_instance(
                await client.retrieve(resource, settings.cindypt_origin))
            if str(instance.get('ri') or identity) != identity:
                raise ValueError('summary identity changed')
            summary = WorkoutSummary.model_validate(content)
            if not summary.status or summary.status.lower() not in ('complete', 'completed'):
                raise ValueError('summary is not completed')
            if isinstance(content, dict) and (content.get('synthetic') is True or
                                              content.get('source') != 'webcam'):
                raise ValueError('summary is not a real webcam workout')
            if not summary.sessionId or summary.workoutId not in (None, summary.sessionId):
                raise ValueError('webcam workout identity does not match its session')
            # A short explicit stop or a crash must never trigger a target-based recommendation.
            if content.get('completionReason') != 'timer' or summary.durationSeconds < 1140:
                self.finish(identity)
                return True
            goal_content, goal_instance = await client.latest_content(
                settings.target_plan_resource, settings.cindypt_origin)
            try:
                goal = Goal.model_validate(goal_content).model_dump()
            except ValidationError as exc:
                raise TinyIoTError('invalid_goal', 'Stored goal is malformed.', 502) from exc
            goal_version = str(goal_instance.get('ri') or goal_instance.get('ct') or '')
            if not goal_version:
                raise TinyIoTError('goal_version_missing', 'Goal has no stable version.', 502)
            workout_id = summary.workoutId or summary.sessionId or identity
            observed = await read_fatigue_history(client, settings.repetition_resource,
                                                  settings.workout_origin)
            history = build_history(observed, [{'workoutId': workout_id,
                'roundsCompleted': summary.rounds, 'durationSeconds': summary.durationSeconds,
                'reps': summary.exercise_counts(), 'source': content['source'],
                'completionReason': content['completionReason'], 'synthetic': False}], goal)
            matching = next((row for row in history['sessions'] if row['sessionId'] == workout_id), None)
            if not matching or not matching['comparable'] or not matching['summaryVerified'] or not matching['suitable']:
                log.warning('Coaching skipped: workout summary lacks matching retained event evidence')
                self.finish(identity)
                return True
            workout = {'workoutId': workout_id, 'roundsCompleted': summary.rounds,
                       'durationSeconds': summary.durationSeconds, 'reps': summary.exercise_counts(),
                       'source': 'webcam', 'completionReason': 'timer'}
            progress = analyze(goal, workout)
            progress.update({'selectionStatus': 'sufficient', 'detectorReliability': 'detector_quality_unknown',
                             'coachingWorkout': matching, 'trainingPlanChangeAppropriate': False})
            advice = recommend(progress)
            advice['goalVersion'] = goal_version
            key = json.dumps([workout_id, goal_version], separators=(',', ':'))
            await client.create_named_recommendation(settings.recommendations_resource,
                                                      settings.cindypt_origin, key, advice)
            self.finish(identity)
            if on_saved:
                on_saved('recommendation', {'sourceWorkoutId': workout_id})
        except (ValueError, ValidationError):
            log.warning('Discarded invalid stored summary for coaching job')
            self.finish(identity)
        except TinyIoTError as exc:
            if exc.code in ('resource_not_found', 'tinyiot_unreachable', 'tinyiot_error',
                            'recommendation_unconfirmed'):
                # A missing goal may be saved later; transport failures recover too.
                self.retry(identity, attempts + 1)
                log.warning('Coaching job deferred: %s', exc.code)
            else:
                self.fail(identity)
                log.error('Coaching job needs operator review: %s', exc.code)
        return True

    async def run(self, client, settings, on_saved=None):
        database_retry_delay = 2
        while True:
            try:
                await self.process_one(client, settings, on_saved)
            except sqlite3.Error:
                # A temporary disk/SQLite failure must not permanently stop
                # the only consumer of the durable coaching queue.
                log.error('Coaching job queue unavailable; retrying local database')
                await asyncio.sleep(database_retry_delay)
                database_retry_delay = min(database_retry_delay * 2, 30)
                continue
            database_retry_delay = 2
            await asyncio.sleep(2)
