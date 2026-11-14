"""Safe, deterministic publication of rule-based fatigue analyses.

This module is intentionally not wired into FastAPI startup or dashboard polling.
Callers must provide an explicitly verified live-session completion signal.
"""

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from .tinyiot import TinyIoTClient, TinyIoTError


class FatiguePublicationError(Exception):
    pass


class FatigueAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: int = Field(alias='schemaVersion', ge=1)
    analysis_id: str = Field(alias='analysisId', min_length=1)
    session_id: str = Field(alias='sessionId', min_length=1)
    exercise: str = Field(min_length=1)
    analysis_window: dict[str, Any] = Field(alias='analysisWindow')
    estimator_version: str = Field(alias='estimatorVersion', min_length=1)
    source_event_sequence: list[int] = Field(alias='sourceEventSequence', min_length=1)
    score: float = Field(ge=0, le=100)
    level: str = Field(pattern=r'^(normal|possible_fatigue|elevated_fatigue)$')
    contributing_signals: list[str] = Field(alias='contributingSignals')
    generated_at: datetime = Field(alias='generatedAt')
    data_quality: dict[str, Any] = Field(alias='dataQuality')
    medical_disclaimer: str = Field(alias='medicalDisclaimer', min_length=1)

    def content(self) -> dict:
        return self.model_dump(mode='json', by_alias=True)


def deterministic_analysis_name(session_id: str, exercise: str,
                                window_id: str, estimator_version: str) -> str:
    identity = json.dumps([session_id, exercise, window_id, estimator_version],
                          separators=(',', ':'), ensure_ascii=True)
    return 'fatigue-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def build_fatigue_analysis(result: dict, analysis_window: dict[str, Any],
                           data_quality: dict[str, Any], *,
                           live_session_id: str | None,
                           live_session_verified: bool,
                           historical: bool = False,
                           estimator_version: str = '9B',
                           generated_at: datetime | None = None) -> FatigueAnalysis:
    """Validate publication eligibility and build a versioned CIN payload."""
    session_id = result.get('sessionId')
    exercise = result.get('exercise')
    level = result.get('fatigueLevel')
    if not live_session_verified:
        raise FatiguePublicationError('live session completion is not verified')
    if historical:
        raise FatiguePublicationError('historical fatigue analysis cannot use the live publication path')
    if not live_session_id or session_id != live_session_id:
        raise FatiguePublicationError('analysis session does not match the verified live session')
    if level == 'unknown' or not data_quality.get('dataSufficient', False):
        raise FatiguePublicationError('unknown or insufficient fatigue result is not publishable')
    if exercise not in ('pullup', 'pushup', 'squat'):
        raise FatiguePublicationError('unsupported exercise')
    window_id = analysis_window.get('windowId')
    sequences = analysis_window.get('sourceEventSequence')
    if not isinstance(window_id, str) or not window_id or not isinstance(sequences, list) or not sequences:
        raise FatiguePublicationError('analysis window identity and source sequence are required')
    if any(type(value) is not int or value < 1 for value in sequences):
        raise FatiguePublicationError('source event sequence is invalid')
    analysis_id = deterministic_analysis_name(session_id, exercise, window_id, estimator_version)
    return FatigueAnalysis(
        schemaVersion=1,
        analysisId=analysis_id,
        sessionId=session_id,
        exercise=exercise,
        analysisWindow=analysis_window,
        estimatorVersion=estimator_version,
        sourceEventSequence=sequences,
        score=float(result.get('fatigueScore')),
        level=level,
        contributingSignals=list(result.get('contributingSignals') or []),
        generatedAt=generated_at or datetime.now(timezone.utc),
        dataQuality=data_quality,
        medicalDisclaimer='Heuristic performance signal; not medically validated fatigue detection.',
    )


class FatiguePublicationService:
    TRANSIENT_ERRORS = {'tinyiot_unreachable', 'tinyiot_error', 'content_unconfirmed',
                        'invalid_tinyiot_response'}

    def __init__(self, client: TinyIoTClient, resource: str, origin: str,
                 max_attempts: int = 3, retry_delay: float = 0.1,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.client = client
        self.resource = resource
        self.origin = origin
        self.max_attempts = max(1, max_attempts)
        self.retry_delay = max(0.0, retry_delay)
        self.sleep = sleep

    async def publish(self, analysis: FatigueAnalysis) -> dict[str, Any]:
        content = analysis.content()
        name = analysis.analysis_id
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                created = await self.client.create_named_verified_content(
                    self.resource, self.origin, name, content)
                return {'created': created, 'duplicate': not created,
                        'resourcePath': f'{self.resource.strip("/")}/{name}',
                        'attempts': attempt, 'analysis': content}
            except TinyIoTError as exc:
                last_error = exc
                if exc.code not in self.TRANSIENT_ERRORS or attempt >= self.max_attempts:
                    raise
                await self.sleep(self.retry_delay * (2 ** (attempt - 1)))
        raise last_error or FatiguePublicationError('publication failed')


class FatiguePublicationJobs:
    """Small separate durable queue; it never touches the coaching job table."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS fatigue_jobs ('
                       'analysis_id TEXT PRIMARY KEY, payload TEXT NOT NULL, '
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

    def enqueue(self, analysis: FatigueAnalysis) -> bool:
        with self._db() as db:
            cursor = db.execute('INSERT OR IGNORE INTO fatigue_jobs(analysis_id, payload, due) VALUES (?, ?, ?)',
                                (analysis.analysis_id, json.dumps(analysis.content(), separators=(',', ':')), time.time()))
        return cursor.rowcount == 1

    def pending(self) -> tuple[str, str, int] | None:
        with self._db() as db:
            return db.execute("SELECT analysis_id, payload, attempts FROM fatigue_jobs "
                              "WHERE state='pending' AND due<=? ORDER BY due LIMIT 1", (time.time(),)).fetchone()

    def finish(self, analysis_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE fatigue_jobs SET state='done' WHERE analysis_id=?", (analysis_id,))

    def retry(self, analysis_id: str, attempts: int, delay: float) -> None:
        with self._db() as db:
            db.execute('UPDATE fatigue_jobs SET attempts=?, due=? WHERE analysis_id=?',
                       (attempts, time.time() + delay, analysis_id))

    async def process_one(self, publisher: FatiguePublicationService) -> bool:
        job = self.pending()
        if job is None:
            return False
        analysis_id, payload, attempts = job
        analysis = FatigueAnalysis.model_validate_json(payload)
        try:
            await publisher.publish(analysis)
        except TinyIoTError as exc:
            if exc.code in publisher.TRANSIENT_ERRORS and attempts + 1 < publisher.max_attempts:
                self.retry(analysis_id, attempts + 1, publisher.retry_delay * (2 ** attempts))
                return True
            raise
        self.finish(analysis_id)
        return True
