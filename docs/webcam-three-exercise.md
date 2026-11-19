# CindySense webcam: three exercise quick run

The existing OpenCV and MediaPipe entry point is `tools/webcam_squat.py`.
Use an environment with `cv2` and `mediapipe` installed and the included
`models/pose_landmarker_lite.task` model. From the repository root in Windows
PowerShell:

```powershell
.\.venv-webcam\Scripts\python.exe -m unittest tools.tests.test_webcam_exercises tools.tests.test_webcam_tinyiot -v
.\.venv-webcam\Scripts\python.exe tools/webcam_squat.py --exercise cindy --duration-minutes 20
```

Click the camera window. Press `1` for PULLUP, `2` for PUSHUP, `3` for SQUAT,
`C` for the Cindy order, `S` to stop and save a Cindy workout early, `R` for a
new session, and `Q` to quit without saving. Switching
exercise modes preserves counts and re-arms the selected detector. The overlay
shows mode, count, phase, and the 20-minute AMRAP clock. The CSV under
`data/recordings/` records measurements locally.

### Diagnose capture and missed fast repetitions

These commands make no TinyIoT requests. Close other camera applications first;
the camera lock prevents simultaneous CindySense probes. Use the same camera,
resolution, lighting, and movement tempo for each comparison:

```powershell
.\.venv-webcam\Scripts\python.exe tools/webcam_capture_probe.py --seconds 60
$env:CINDYSENSE_PERF_LOGGING = '1'
.\.venv-webcam\Scripts\python.exe tools/webcam_squat.py --exercise cindy --duration-minutes 3 --voice-events-port 0
```

The first command measures capture without MediaPipe, detection, voice, or
network publishing. For the second, manually count slow and fast sets of each
exercise. The runner prints `[PERF]` every five seconds and `[PERF FINAL]` on
exit. Its CSV records detector phase, rejection reason, rep event, camera read
failures, dropped unread frames, pose inference time, detector-update gap,
and per-exercise detected-rep gap. Compare those rows against the manual count
and note the exact time of any observed freeze. `--perf-gap-ms` sets the
detector-gap alert threshold (100 ms by default). Remove
`--voice-events-port 0` and connect the local dashboard to compare browser
voice on the same Cindy sequence. Browser speech playback is outside the Python
frame loop; `voiceEmitMs` measures only local event enqueueing.

`capturedAtUtc` and `captureElapsedMs` mark when OpenCV `read()` returned, not
sensor exposure. `readReturnToInferenceMs` is the delay after that return;
neither it nor `staleFrame` proves sensor frame age. The legacy `frameAgeMs`
CSV column is left blank to avoid implying a sensor timestamp. The camera
backend's reported buffer size and `bufferSet` return value are diagnostic,
not proof that a requested buffer size took effect. The latest-frame slot
reports overwritten unread frames; do not interpret this as confirmed lost
physical repetitions.

Network-on comparisons must use an explicitly disposable local TinyIoT CSE
with separately authorized test containers and originators. Do not point a
diagnostic run at production TinyIoT resources or ACPs. The default runner
without `--tinyiot` is the safe network-off comparison. Retain the CSV and
console output for the same manually counted sets before changing camera
buffering, detection thresholds, or cooldowns.

For a connected workout, first verify the existing `Webcam_AE/PoseData`,
`CindySense_AE/RepetitionCount`, and `CindySense_AE/WorkoutSummary` resources
and non-admin publisher permissions. Configure verified origins in
`backend/.env` and start the workout through the dashboard. For a manual
runner session, pass the same verified origins:

```powershell
$eventOrigin = 'your-verified-event-origin'
$summaryOrigin = 'your-verified-summary-origin'
.\.venv-webcam\Scripts\python.exe tools/webcam_squat.py --exercise cindy --tinyiot --tinyiot-origin CWebcam --tinyiot-event-origin $eventOrigin --tinyiot-summary-origin $summaryOrigin
```

The runner rejects `CAdmin` for event and summary publishing. Do not run
`setup`, `test`, or a workout publisher as a read-only preflight; those commands
can create oneM2M resources or content instances.

Use `--tinyiot-base` if TinyIoT is not at the default local address. Pose
readings go to `Webcam_AE/PoseData` about once per second. Completed
repetition events take priority on a separate worker and go to
`CindySense_AE/RepetitionCount` with `source=webcam`, a unique event ID and
sequence, session ID, UTC timestamp, exercise, and that exercise's cumulative
count. Stable CIN names and exact readback prevent a retry from creating a
second event. ESP32 events have `source=mpu6050`; keep sources separate when
comparing accuracy. The dashboard selects one source at a time.

When a Cindy session reaches its timer or the operator presses `S`, the runner
posts one completed snapshot to the existing `CindySense_AE/WorkoutSummary`
container after flushing repetition events. It includes `sessionId`,
`status: "completed"`, `completionReason`, final `pullups`, `pushups`, `squats`,
`rounds`, and `durationSeconds`. A manual-mode session is not summarized.
`Q`, reset, camera failure, and unexpected exceptions do not produce a
completed summary. The writer uses a stable name derived from the session ID,
reads back the exact content, and retains pending work in the local event journal
for a later retry. Check `summaryPublication`, `eventJournalPending`, and
`summaryPending` in worker status before treating the workout as stored. The optional
`--tinyiot-summary-origin` defaults to `--tinyiot-event-origin`; configure it
separately if an approved dedicated summary writer is available. The optional
`--summary-container` changes only the target path; it never creates a
container. CindyPT reads the new completed snapshot on its next progress GET;
a verified timer summary can also enqueue an automatic coaching job.

The camera thread never waits for TinyIoT. Pose readings use a five-slot
queue and stale readings are discarded. Each validated repetition event is
committed to `data/recordings/webcam_pending_events.sqlite3` before network
delivery. The single event worker retries in journal order with backoff and
marks events delivered only after exact TinyIoT readback. Pending events replay
after a crash; an existing JSON shutdown spool is imported once. Journal
capacity or disk errors stop the workout rather than silently dropping an
event. Failed events block later delivery until investigated. Check the
pending/delivered/retried/failed diagnostics before starting another workout.
The camera loop exits when the 20-minute Cindy timer finishes.
The completed summary is sealed locally with its session event count. If any
event or summary readback is pending, it is not reported as persisted and is
retried when the webcam publisher next starts. The completion wait is bounded;
check `summaryPending` and `eventJournalPending` diagnostics before a new run.
The webcam client connects to local `localhost` through `127.0.0.1` on
Windows, avoiding the measured IPv6 connection fallback delay. Configure
a different host explicitly when TinyIoT is remote.
In the local probe, the previous existence GET and event POST took about
2,095 ms and 2,064 ms; after the IPv4 connection fix they took 6 ms and
16 ms. These are isolated HTTP measurements, not webcam-to-dashboard latency.

## Thresholds to validate on video

The existing squat knee-angle thresholds remain 115° down / 155° up.
Push-ups retain 95° / 155° elbow angles, at least 145° shoulder–hip–ankle
alignment, and 0.035 frame-height shoulder descent. Return is measured from
each descent peak, so a shifted top baseline cannot hold the phase in DOWN
forever. A prolonged extended-elbow pose with no verified shoulder rise
rejects the candidate and re-arms at the current top.

Pull-ups start from at least 145° elbow extension, require at least 35° of
flexion, and accept a flexed angle up to 120° only with stronger vertical
travel. Below 100° flexion the shoulder-rise threshold is 0.040 frame heights;
at shallower flexion it is 0.070. Visible head rise is optional supporting
evidence. The nose and wrist image heights are no longer hard gates; elbow
landmarks remain required. A 0.10 frame-width lateral excursion rejects a
swinging candidate when both shoulders are visible. Both arm detectors use
three stable frames for each phase, brief occlusion tolerance, and a ten-frame
cooldown. Squat thresholds and logic are unchanged. These arm thresholds are
provisional and require labeled real repetitions from the intended camera view.

The long recording `webcam_workout_20261106T124307Z.csv` logged push-up 57 at
19:58. The old loop then passed the 20-minute deadline, stopped updating the
counter, but continued displaying its DOWN phase for over a minute. The new
loop stops at the deadline. A replay of stored angle and shoulder-position
columns is useful for regression inspection but lacks head/lateral landmarks
and ground-truth labels; it is not an accuracy measurement.

For physical validation, film five controlled repetitions of each exercise,
rest, incomplete movements, camera occlusion, and a short pause. Compare
actual and counted repetitions, then disconnect TinyIoT briefly while the
camera runs and verify queued events drain once it returns. Synthetic tests
check state-machine behavior; they do not establish real-world accuracy.

## Live synchronization check

From the repository root, inspect the existing repetition container's retention
and subscription status through read-only backend endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/workout/snapshot | ConvertTo-Json -Depth 8
```

Do not change retention during a backend preflight. The backend retrieves oneM2M
`rcn=6` references in pages of 100 and reads newly seen events. Its snapshot
reports `retention.mni`, `retention.mbs`, `retainedInstanceCount`,
`retentionAtCapacity`, `retrievedAt`, and `retrievalMs`. The dashboard refreshes
its stored snapshot on validated events, with a five-second fallback poll, and
polls webcam pose status every 1.5 seconds. A separate publisher thread prioritizes repetition events;
PoseData readings are rate limited and stale readings can be dropped.

To measure a real repetition, compare its `[REP] detectedAt` and `[TinyIoT]
Event confirmed` log lines by `eventId`. Then
compare the FastAPI snapshot `retrievedAt` and the browser console's
`[CindySense] dashboard rendered` timestamp for the same session and count.
New webcam CSVs include detector reasons, optional head/lateral measurements,
event IDs, and queue depth so a missed physical repetition can be investigated.
The dashboard shows webcam queue depth, retries, overflow, pending count
estimate, stale pose status, and session mismatch. Pending count compares
PoseData's absolute exercise count with confirmed RepetitionCount events;
it can temporarily lag by a pose upload interval. A completed 20-minute webcam
run was recorded, but it did not include independently
timed physical repetitions or timestamped dashboard captures, so physical
accuracy and detection-to-render latency remain unverified.
With 121 retained local events, an isolated backend restart took 4.9 seconds
to fill its cache; three subsequent snapshot requests took 262-266 ms each.
Startup recovery is therefore slower than steady-state updates.
