# CindySense full-workout readiness checklist

This checklist is for the supervised 20-minute workout. It is intentionally read-only until the operator clicks **Start Workout** tomorrow. Do not run the webcam tonight.

## 1. Start or verify WSL services

Check first; do not start a second server if port 3001 is already listening:

```powershell
Test-NetConnection 127.0.0.1 -Port 3001
wsl.exe -e bash -lc 'pg_isready -h 127.0.0.1 -p 5432'
```

If TinyIoT is not running, start the approved production binary in WSL:

```powershell
wsl.exe -e bash -lc '/home/eppy-04/tinyIoT-prod-build-20261115/server -p 3001'
```

Use the original source binary only if that is the approved deployment for the machine; never run both binaries on port 3001.

## 2. Start FastAPI

From the repository root, verify the environment file first:

```powershell
Get-Content .\backend\.env
Test-Path .\.venv-webcam\Scripts\python.exe
```

The current configuration uses `http://127.0.0.1:3001/TinyIoT`, `CDashboard` for reads, `CWebcam` for webcam event and summary publishing, `CCindyPT` for CindyPT resources, and `CINDYSENSE_PERF_LOGGING=1`. Start one backend process:

```powershell
& .\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

## 3. Start the dashboard

In a second PowerShell window:

```powershell
Set-Location .\dashboard
npm.cmd run dev -- --host 0.0.0.0
```

Open the URL Vite prints, normally `http://localhost:5173` or `http://localhost:5174`.

## 4. Read-only health checks

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/workout/webcam-worker | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/integration/metrics | ConvertTo-Json
```

Expected pre-workout state: TinyIoT reachable, worker `idle` or `stopped`, no webcam Python process, and no active `data/webcam-worker-ipc/stop` race. A stopped status from a prior completed session is safe; the next Start creates a new unique `webcam-<UTC>-<8 hex>` session ID.

## 5. Confirm local webcam prerequisites without opening the camera

```powershell
$py = 'C:\Users\USER\Documents\CindySense-GitHub\.venv-webcam\Scripts\python.exe'
& $py -c "import sys, cv2, mediapipe; print(sys.executable); print(cv2.__version__); print(mediapipe.__version__)"
Test-Path .\models\pose_landmarker_lite.task
Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'python' -and $_.CommandLine -match 'webcam_squat|webcam_worker'}
```

The model must be present and the interpreter must be the configured `.venv-webcam` interpreter. Do not open another camera application.

## 6. Voice Coach

The dashboard Voice Coach is enabled by its ON control and browser speech permission. The worker exposes the loopback SSE stream on `127.0.0.1:8765` by default. Click the dashboard once before the workout so browser audio is unlocked. If port 8765 is occupied, stop the conflicting local process or use `--voice-events-port 0` only when voice is intentionally disabled.

## 7. Start and monitor the workout

Select **Webcam**, confirm the worker is idle/stopped, then click **Start Workout** once. Do not launch `tools/webcam_squat.py` separately. The backend launches it with `--exercise cindy --duration-minutes 20 --tinyiot`, the configured non-admin origins, and dashboard IPC.

During the session, verify:

- the preview is changing and the worker is `running`;
- the timer counts down from 20:00;
- counts advance in the order of 5 pull-ups, 10 push-ups, 15 squats per round;
- the active session ID is stable;
- queue depth stays bounded and `eventOverflow` remains zero;
- the dashboard and `GET /api/workout/snapshot` show the same confirmed counts.

## 8. Safe stop

Use the dashboard **Stop Workout** button. Wait for `stopping` and then `stopped`; do not kill unrelated Python processes. An explicit stop publishes a completed summary marked `completionReason: explicit_stop`, but CindyPT will treat it as limited practice data and will not make an unsupported 20-minute target adjustment.

If the worker remains uncertain, record the reported PID and worker log, then stop before starting another session. Do not remove the IPC directory while the PID is alive.

## 9. Verify completion and event-driven CindyPT

After a natural 20-minute timer completion:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/workout/snapshot | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/cindypt/progress | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/cindypt/recommendations | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/integration/metrics | ConvertTo-Json -Depth 8
```

Read the latest summary and resource counters with the repository's configured backend origin. The expected summary is under `CindySense_AE/WorkoutSummary` and must contain the same session ID, `status: completed`, `source: webcam`, `completionReason: timer`, counts, rounds, and approximately 1,200 seconds. `Summary_Sub` should increment FastAPI's validated callback metric. The coaching job should move from pending to done and the dashboard should refresh through the integration SSE stream.

## 10. Collect evidence

```powershell
Get-ChildItem .\data\recordings\webcam_workout_*.csv | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content .\data\webcam-worker-ipc\worker.log -Tail 200
Get-Content .\data\webcam-worker-ipc\status.json
```

Copy the session ID, final counts, rounds, completion reason, performance metrics, queue retries/overflow, summary CIN name, notification metrics, and CindyPT recommendation into the template in [edge-architecture.md](./edge-architecture.md). Do not edit historical CINs.

## 11. Troubleshooting

- **TinyIoT unreachable:** check port 3001 and `pg_isready`; do not start a duplicate CSE.
- **Worker refuses to start:** verify `CINDYSENSE_WEBCAM_PYTHON`, model path, and that no other camera worker owns `data/webcam-camera.lock`.
- **Worker error:** inspect `data/webcam-worker-ipc/worker.log`; the dashboard reports the worker PID and failure state.
- **No notification:** verify the existing `Summary_Sub` destination and FastAPI notification token configuration; do not recreate the subscription.
- **No recommendation:** inspect selection status. Short, explicit-stop, synthetic, unverified, or detector-unreliable sessions are intentionally excluded from target changes.
- **Dashboard stale after SSE loss:** reload the CindyPT page; bounded polling reconciles stored results without touching the webcam worker.

## Readiness classification

- **VERIFIED tonight:** TinyIoT/FastAPI health, resource tree, ACP read permissions, GRP read/fan-out, existing subscription destinations, interpreter/imports/model presence, worker stopped, and automated tests.
- **MOCK-TESTED:** notification parsing, duplicate suppression, durable coaching jobs, recommendation publication with `CCindyPT`, and dashboard refresh behavior.
- **CONFIGURED BUT UNVERIFIED:** live `CWebcam` CREATE/RETRIEVE POST path and a genuine completed timer summary. A live write is intentionally deferred until tomorrow's supervised workout.
- **BLOCKED:** none for the supervised start, provided the preflight checks above pass. Zero event loss cannot be guaranteed because in-memory events can be lost by an unclean process or machine failure.
