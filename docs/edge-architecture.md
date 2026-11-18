# CindySense edge and oneM2M architecture

## Runtime placement

| Component | Host | Responsibility |
|---|---|---|
| Webcam capture, OpenCV, MediaPipe, exercise classification | Windows webcam host | Reads the camera, keeps the newest frame, estimates pose, classifies movement, and confirms repetitions locally. |
| Webcam preview worker and IPC | Windows webcam host | Encodes annotated JPEG previews and publishes the newest frame/status to the FastAPI worker IPC directory. |
| FastAPI and durable CindyPT job queue | Windows host, port 8000 | Controls the worker, serves the API/MJPEG preview, validates oneM2M notifications, selects trustworthy workouts, and processes queued coaching jobs. |
| TinyIoT CSE and PostgreSQL | WSL, port 3001 / PostgreSQL 5432 | Stores oneM2M resources, CINs, subscriptions, ACPs, and retained workout events. |
| React/Vite dashboard | Windows browser, port 5173 or 5174 | Displays status, preview, authoritative workout counts, SSE updates, history, and CindyPT results. |

Local pose inference is edge computing because raw camera frames are processed on the webcam host before they leave the machine. TinyIoT receives structured pose diagnostics and confirmed repetition events, not raw video. This avoids sending camera bandwidth through the CSE and keeps counting independent of oneM2M round-trip latency.

## Event path

`Camera -> latest-frame buffer -> MediaPipe -> exercise state machine -> confirmed repetition -> SQLite event journal -> RepetitionCount CIN -> exact readback -> RepCount_Sub -> FastAPI SSE/dashboard`

On timer completion or an explicit safe stop, the runner publishes one idempotent `WorkoutSummary` CIN keyed by the webcam session ID. `Summary_Sub` delivers it to FastAPI, which validates the stored CIN, queues a durable SQLite coaching job, and later publishes a deterministic CindyPT recommendation when the retained event evidence is sufficient.

## Disconnect and loss behavior

- Pose inference and local counting continue while TinyIoT is temporarily unavailable.
- Noncritical pose telemetry uses a five-item latest-value queue and stale readings are dropped.
- Confirmed repetition events use a bounded, durable SQLite outbox at `data/recordings/webcam_pending_events.sqlite3`; the single event worker retries in order and acknowledges only after exact oneM2M readback. The former JSON shutdown spool is imported on startup.
- Completed webcam summaries are sealed in the same outbox, checked against session event counts, and published only after all that session's events are readback-confirmed. Pending summaries replay when the webcam publisher next starts; waits at completion are bounded.
- Optional `CINDYSENSE_SUMMARY_RECONCILIATION_ENABLED` scans retained workout summaries with the CindyPT read origin and idempotently enqueues missed coaching jobs. It is disabled by default because coaching may later publish recommendations.
- Undelivered journal events survive a clean shutdown or worker crash. A crash between local repetition detection and journal commit can still lose that detection.
- Journal capacity or disk failures stop event acceptance and are surfaced as worker errors; they are not reported as successful delivery.
- Preview uses latest-frame semantics and is independent of the detector path.

The project does not implement an ETSI MEC platform. It implements local edge inference plus an HTTP oneM2M CSE integration.

## Tomorrow performance template

Record these values from the worker status, CSV, and log without changing detector settings:

| Metric | Value | Source |
|---|---:|---|
| Session ID |  | `status.json` / worker log |
| Duration and completion reason |  | `WorkoutSummary` |
| Capture/inference frames |  | `performance.frames` |
| Average/max frame gap |  | CSV `frameGapMs` |
| Average/max pose latency |  | CSV `poseMs` and worker performance |
| Dropped camera frames |  | `performance.droppedFrames` |
| Preview encode/write average |  | `previewEncodeAvgMs`, `previewWriteAvgMs` |
| Preview frame age |  | `previewFrameAgeMs` |
| Event queue peak/retries/overflow |  | worker log and status |
| Confirmed event count and latency |  | worker log / `RepetitionCount` |
| Counts and completed rounds |  | summary, snapshot, dashboard |
