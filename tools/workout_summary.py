"""Final Cindy workout snapshots and bounded, idempotent delivery."""

import math
import re
import time

if __package__:
    from .webcam_tinyiot import (RetryableSummaryError, SUMMARY_CONTAINER,
                                 TinyIoTConnectionError, TinyIoTError)
else:
    from webcam_tinyiot import (RetryableSummaryError, SUMMARY_CONTAINER,
                                TinyIoTConnectionError, TinyIoTError)

WEBCAM_SESSION_ID = re.compile(r'^webcam-\d{8}T\d{6}Z-[a-f0-9]{8}$')
TARGETS = {'pullup': 5, 'pushup': 10, 'squat': 15}


def completed_summary(session_id, workout, totals, reason):
    if reason not in ("timer", "explicit_stop"):
        raise ValueError("Only a timer finish or explicit stop completes a workout")
    if not isinstance(session_id, str) or not WEBCAM_SESSION_ID.fullmatch(session_id):
        raise ValueError("A completed webcam workout needs its original webcam session ID")
    if any(type(totals.get(name)) is not int or totals[name] < 0 for name in TARGETS):
        raise ValueError("A completed workout needs final exercise counts")
    if (type(workout.rounds) is not int or workout.rounds < 0 or
            workout.rounds > min(totals[name] // target for name, target in TARGETS.items())):
        raise ValueError("Completed rounds exceed the observed exercise totals")
    if not isinstance(workout.elapsed, (int, float)) or not math.isfinite(workout.elapsed) or workout.elapsed < 0:
        raise ValueError("A completed workout needs a valid elapsed duration")
    return {
        "sessionId": session_id, "status": "completed", "source": "webcam",
        "completionReason": reason,
        "rounds": workout.rounds, "pullups": totals["pullup"],
        "pushups": totals["pushup"], "squats": totals["squat"],
        "durationSeconds": max(1, math.ceil(workout.elapsed)),
    }


class SummaryPublisher:
    def __init__(self, client, container=SUMMARY_CONTAINER, attempts=3, retry_delay=0.25):
        self.client = client
        self.container = container
        self.attempts = attempts
        self.retry_delay = retry_delay
        self.persisted = {}

    def publish(self, summary):
        session_id = summary["sessionId"]
        previous = self.persisted.get(session_id)
        if previous is not None:
            if previous != summary:
                raise TinyIoTError(f"Session {session_id} was already saved with different results")
            return "already_persisted"
        last_error = None
        for attempt in range(self.attempts):
            try:
                result = self.client.publish_summary(summary, self.container)
                self.persisted[session_id] = summary.copy()
                return result
            except (TinyIoTConnectionError, RetryableSummaryError) as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(self.retry_delay * (2 ** attempt))
        raise RetryableSummaryError(
            f"Workout summary not confirmed after {self.attempts} attempts: {last_error}") from last_error
