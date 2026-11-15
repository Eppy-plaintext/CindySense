# Workout intelligence

The backend reads retained `CindySense_AE/RepetitionCount` CINs with the configured
workout read origin. It excludes explicit synthetic and diagnostic events, validates
structured webcam events, and groups them by session. `GET /api/workout/performance`
returns observed pace for one selected session. `GET /api/workout/history` joins those
groups to completed `CindySense_AE/WorkoutSummary` CINs by exact session ID. Neither
endpoint writes to TinyIoT.

## Calculation

- An interval is the elapsed time between two consecutive validated events for
  the same exercise and round, divided by the increase in the repetition counter.
- The median interval for that exercise is the baseline. Intervals above 2.5
  times the median are excluded from pace and shown as possible rest gaps. Such
  gaps may instead be missed camera detections, so they are not measured rest.
- Pace is the mean of remaining intervals, reported as seconds per repetition
  and repetitions per minute. Pace consistency is the population coefficient of
  variation. Early and late halves are compared; a late pace more than 25%
  slower yields `pace_decreasing`. At least three pace intervals are required.
- `unexplained_long_gap` is used when a long gap remains but no slowdown is
  established. It must not be interpreted as medical fatigue.
- A completed round comes from a matching completed summary, never the round
  number on a single repetition event. Only timer-completed workouts of at least
  19 minutes, with a webcam source and matching retained counts, are compared
  as equivalent 20-minute sessions. Explicit stops, missing summaries, summary
  mismatches, test data, and summaries without retained events remain visible
  with separate statuses.

The existing fatigue estimator remains a separate non-medical heuristic. The
repetition CIN format has no pose-quality or landmark-loss field; therefore
performance results mark detector quality `unverified`, and the live fatigue
endpoint returns `unknown` with reason `detector_quality_unverified`. A long
interval alone must not trigger a fatigue alert or an automatic plan change.

## CindyPT and oneM2M

`Summary_Sub` notifications enqueue the existing durable coaching job, keyed by
summary CIN identity. The job writes a deterministic recommendation CIN to
`CindyPT_AE/AIRecommendations`; retries cannot create a second CIN for the same
workout and goal version. Explicit synthetic or non-webcam summaries are not
queued for coaching. The advice includes an explanation and proposed round
target. Saved `TrainingPlans`, `WorkoutSchedule`, and `RecoveryRecommendations`
continue to use the existing preview and explicit-save workflow.

The existing `CindyPT_AE/FatigueAnalysis` container and publication service
remain available. No performance or fatigue CIN is published automatically:
retained repetition events lack verified detector-quality evidence, and the
current completion subscription has no quality payload. The proposed new
`PerformanceMetrics` and `FatigueAlerts` containers were not created. This
avoids writing misleading alerts or changing production ACPs and subscriptions.
`RepCount_Sub` and `Summary_Sub` continue to provide event-driven updates.

The dashboard consumes performance data already present in the cached fatigue
response. It adds no extra live polling request to the webcam worker.
