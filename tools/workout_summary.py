"""Final Cindy workout snapshots and bounded, idempotent delivery."""

import math
import time

if __package__:
    from .webcam_tinyiot import (RetryableSummaryError, SUMMARY_CONTAINER,
                                 TinyIoTConnectionError, TinyIoTError)
else:
    from webcam_tinyiot import (RetryableSummaryError, SUMMARY_CONTAINER,
                                TinyIoTConnectionError, TinyIoTError)


def completed_summary(session_id, workout, totals, reason):
    if reason not in ("timer", "explicit_stop"):
        raise ValueError("Only a timer finish or explicit stop completes a workout")
    if not session_id or any(not isinstance(totals.get(name), int) or totals[name] < 0
                             for name in ("pullup", "pushup", "squat")):
        raise ValueError("A completed workout needs an ID and final exercise counts")
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
        raise TinyIoTError(f"Workout summary not confirmed after {self.attempts} attempts: {last_error}") from last_error
