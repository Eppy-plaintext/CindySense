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

To use the existing oneM2M resources, first check/create the webcam AE and
PoseData container with the already configured TinyIoT originators:

```powershell
.\.venv-webcam\Scripts\python.exe tools/webcam_tinyiot.py --origin CWebcam setup
.\.venv-webcam\Scripts\python.exe tools/webcam_tinyiot.py --origin CWebcam test
.\.venv-webcam\Scripts\python.exe tools/webcam_squat.py --exercise cindy --tinyiot --tinyiot-origin CWebcam --tinyiot-event-origin CAdmin
```

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
reads back the exact content, and retries transient failures up to three times.
The terminal prints `PERSISTED` only after readback; `PERSISTENCE UNCONFIRMED`
means the named CIN needs a read-only check before any manual retry. The optional
`--tinyiot-summary-origin` defaults to `--tinyiot-event-origin`; configure it
separately if an approved dedicated summary writer is available. The optional
`--summary-container` changes only the target path; it never creates a
container. CindyPT reads the new completed snapshot on its next progress GET,
while recommendation generation still requires the dashboard button.

The camera thread never waits for TinyIoT. Pose readings use a five-slot
queue and stale readings are discarded. Repetition events use a 1,024-slot
RAM queue with retries and explicit overflow/retry/confirmation diagnostics.
At normal shutdown, the publisher has a bounded flush period and saves any
unconfirmed events to `data/recordings/webcam_pending_events.json` for
idempotent replay on the next run. A crash before shutdown, a full queue, or
a spool-write failure can still lose an event; watch the diagnostics. The
camera loop exits when the 20-minute Cindy timer finishes.
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

In Windows PowerShell, from the repository root, check and raise the existing
repetition container's retention limit before a workout:

```powershell
.\.venv-webcam\Scripts\python.exe tools/webcam_tinyiot.py retention --admin-origin CAdmin --min-events 1000
```

This updates only `CindySense_AE/RepetitionCount` when its `mni` is too small.
It does not restore events already evicted. The backend retrieves oneM2M
`rcn=6` references in pages of 100 and reads newly seen events. Its snapshot
reports `retention.mni`, `retention.mbs`, `retainedInstanceCount`,
`retentionAtCapacity`, `retrievedAt`, and `retrievalMs`. The dashboard polls
the snapshot every 750 ms after each response and webcam pose status every
1.5 seconds. A separate publisher thread prioritizes repetition events;
PoseData readings are rate limited and stale readings can be dropped.

To measure a real repetition, compare its `[REP] detectedAt`, `[TinyIoT]
Event queued`, and `[TinyIoT] Event confirmed` log lines by `eventId`. Then
compare the FastAPI snapshot `retrievedAt` and the browser console's
`[CindySense] dashboard rendered` timestamp for the same session and count.
New webcam CSVs include detector reasons, optional head/lateral measurements,
event IDs, and queue depth so a missed physical repetition can be investigated.
The dashboard shows webcam queue depth, retries, overflow, pending count
estimate, stale pose status, and session mismatch. Pending count compares
PoseData's absolute exercise count with confirmed RepetitionCount events;
it can temporarily lag by a pose upload interval. A full 20-minute webcam
and TinyIoT run is still required to measure detection-to-render latency and
verify the 1-2 second target in the actual environment.
With 121 retained local events, an isolated backend restart took 4.9 seconds
to fill its cache; three subsequent snapshot requests took 262-266 ms each.
The 750 ms poll begins after each response. Startup recovery is therefore
slower than steady-state updates.
