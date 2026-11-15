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

`Camera -> latest-frame buffer -> MediaPipe -> exercise state machine -> confirmed repetition -> bounded TinyIoT event queue -> RepetitionCount CIN -> RepCount_Sub -> FastAPI SSE/dashboard`

On timer completion or an explicit safe stop, the runner publishes one idempotent `WorkoutSummary` CIN keyed by the webcam session ID. `Summary_Sub` delivers it to FastAPI, which validates the stored CIN, queues a durable SQLite coaching job, and later publishes a deterministic CindyPT recommendation when the retained event evidence is sufficient.

## Disconnect and loss behavior

- Pose inference and local counting continue while TinyIoT is temporarily unavailable.
- Noncritical pose telemetry uses a five-item latest-value queue and stale readings are dropped.
- Confirmed repetition events use a bounded 20,000-item in-memory queue with retry backoff and a shutdown spool at `data/recordings/webcam_pending_events.json`.
- A clean shutdown saves undelivered events for the next run; a process or machine crash can still lose events that were only in RAM or in flight.
- Queue overflow is counted and logged as `EVENT QUEUE FULL`; the local counter is not silently converted into a stored event.
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
