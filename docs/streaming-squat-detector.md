# Phase 1 streaming squat detector

The read-only detector in [backend/streaming_squat_detector.py](../backend/streaming_squat_detector.py)
accepts sequential collector-style samples. It does not contact TinyIoT or publish
repetition events. Use one instance per recording session.

```python
from backend.streaming_squat_detector import StreamingConfig, StreamingSquatDetector

detector = StreamingSquatDetector("session-001", StreamingConfig(orientation="auto"))
events = detector.process_batch(cin_samples, batch_id=cin_resource_id)
state = detector.get_state()
# Call detector.reset_session("session-002") before a new recording session.
```

Each sample must contain `sample_time_seconds`, `ax`, `ay`, `az`,
`gx`, `gy`, and `gz`. The collector's optional `sample_t` helps detect a
device clock reset. Units are seconds, m/s², and degrees/s. Pass the CIN
resource identifier or full CIN path as `batch_id`; do not pass `X-M2M-RI`,
which identifies an HTTP request rather than a stored batch. If a batch has
no identity, repeated sample timestamps still prevent duplicate events.

The filter is a stateful, causal one-pole low-pass filter. The default
`adaptive` calibration looks for quiet samples. For a workout with a labelled
standing lead-in, select `calibration_mode="initial_standing"`. It requires a
continuous stable standing window before enabling counts; set its duration with
`standing_calibration_seconds`. The replay tool accepts
`--calibration-mode initial_standing --standing-calibration-seconds 5`.

If calibration quality is insufficient, the state is `uncertain`, the reason
and measured quality are available from `get_state()`, and confirmed counts
remain disabled. Do not interpret zero counts in an uncertain session as a
detector miss. The detector only confirms a repetition after a complete causal
tilt cycle passes timing, raw tilt return, and directional gyro checks. A
rejected movement is diagnostic only and does not increment the count.
Uncertain movement candidates are surfaced separately with their evidence and
reason; they are not confirmed repetitions. The automatic orientation becomes
known only after a confirmed full cycle. A gap over 40 ms aborts an incomplete
cycle. A detected device clock reset restarts calibration while preserving the
session's confirmed count. No missing samples are synthesized.

For a local, sequential CSV replay:

```sh
python tools/replay_squat_stream.py data/recordings/squat_slow_08.csv
python tools/replay_squat_stream.py data/recordings/rest_validation_03.csv --batch-size 37
python tools/replay_squat_stream.py data/recordings/squat-slow-01.csv --calibration-mode initial_standing --standing-calibration-seconds 5
```

Batch size changes when an event becomes available to a caller, but not
which sample completes it. The replay prints bottom-to-confirmation time and
the additional wait until the end of its batch. Neither is an independently
measured biomechanical event latency.

This is a Phase 1 prototype. Causal calibration and filtering differ from
the offline detector's whole-recording calibration and zero-phase filtering.
An isolated non-squat movement can still look like one complete squat cycle;
the module must be validated further before it drives live workout counts.
