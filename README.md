# CindySense

CindySense is a three-person university hackathon prototype for IoT workout tracking. An ESP32 with an MPU6050 sends sensor readings to TinyIoT (oneM2M). FastAPI reads stored oneM2M content and serves a React/Vite dashboard. Its Cindy PT backend produces rule-based recommendations from completed workout summaries.

**Current state:** the dashboard includes a separate demo mode. In webcam mode, its timer, exercise counters, and rounds follow the local OpenCV/MediaPipe worker; stored TinyIoT panels can lag while events are being confirmed. The Cindy PT page reads stored goals, completed-workout progress, and rule-based recommendations through FastAPI. The firmware sends sensor readings but does not yet implement exercise recognition or repetition counting.

## Components

| Folder | Purpose |
| --- | --- |
| [backend](backend/) | FastAPI bridge, oneM2M client, Cindy PT logic, and tests |
| [dashboard](dashboard/) | React/Vite user interface |
| [firmware](firmware/) | ESP32/MPU6050 Arduino sketch |
| [docs](docs/) | Architecture, API, TinyIoT setup, and team workflow |
| [tools](tools/) | Read-only, labelled sensor CIN collection |

TinyIoT is an **external dependency**. See [TinyIoT setup](docs/tinyiot-setup.md); its source, database, and configuration are not part of this repository.

## Suggested team ownership

- **Firmware and sensing:** ESP32 wiring, sensor capture, and future recognition logic.
- **Backend and oneM2M:** FastAPI, resource contracts, Cindy PT integration, and tests.
- **Dashboard and experience:** React views, API client, mock data, and interface checks.

Review API contracts together before changing a payload or resource path. Any teammate can review or help across components. See [collaboration](docs/collaboration.md).

## Local development

Prerequisites: Python 3.10+, Node.js/npm compatible with the versions in [package.json](dashboard/package.json), and an existing TinyIoT installation for connected reads. For firmware, use an ESP32 Arduino toolchain plus Adafruit MPU6050 and Adafruit Unified Sensor libraries.

### FastAPI

From the repository root:

```sh
python -m venv backend/.venv
# Activate the environment for your shell.
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
# Set the local TinyIoT URL and read originators in backend/.env.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

On Windows PowerShell, use `py -m venv backend/.venv`, `Copy-Item backend/.env.example backend/.env`, and the environment's `Scripts/python.exe` for installation and startup. Keep `backend/.env` local. When TinyIoT is unavailable, `GET /api/health` still reports backend availability; TinyIoT-backed endpoints report connection errors.

### Dashboard

```sh
cd dashboard
npm ci
npm run dev
```

Open `http://localhost:5173`. The browser calls FastAPI on port 8000 by default. For local Windows development, set `VITE_API_BASE_URL=http://127.0.0.1:8000` in `dashboard/.env.local` (copy [the dashboard example](dashboard/.env.example)), then restart Vite so it loads the value. If the dashboard reports Backend Offline while `GET /api/health` succeeds, check the URL in the browser Network tab; it must use port 8000, and FastAPI must allow the dashboard's `localhost:5173` or `127.0.0.1:5173` origin. Vite environment values are visible in the browser, so never put credentials, tokens, or oneM2M originators there. The dashboard demo views work without TinyIoT; connected panels show unavailable status until FastAPI and TinyIoT are reachable.

### Firmware

Copy [config.example.h](firmware/config.example.h) to `firmware/config.h`, fill in your local WiFi settings, TinyIoT URL, and an authorized **device-specific** oneM2M originator, then open [cindysense.ino](firmware/cindysense.ino) in the Arduino IDE. Keep `config.h` local. The sketch's upload behavior is unchanged apart from reading these settings from the header. Do not use an administrative originator for a shared device.

### Cindy PT

[cindypt.py](backend/cindypt.py) provides rule-based coaching through FastAPI. Configure `TINYIOT_CINDYPT_ORIGIN` server-side for `CindySense_AE/WorkoutSummary`, `CindyPT_AE/FitnessGoals`, and `CindyPT_AE/AIRecommendations`. A verified completed-summary notification queues a job in local SQLite. The worker publishes idempotent advice only for a full timer workout with matching retained repetition evidence and a stored goal; a short explicit stop does not generate advice. Manual generation remains available. Run a single FastAPI worker with a persistent writable `CINDYSENSE_COACHING_DB` location. To recover missed summary callbacks, explicitly enable `CINDYSENSE_SUMMARY_RECONCILIATION_ENABLED`; the scan itself is read-only but a recovered job can publish advice. See [full-workout readiness](docs/full-workout-readiness.md) for the current preflight.

The subscription monitor checks configured subscriptions at startup and periodically. Its recovery setting defaults to enabled and may create a missing subscription. Set `CINDYSENSE_SUBSCRIPTION_RECOVERY_ENABLED=false` for read-only inspection of an existing TinyIoT resource tree.

The webcam runner prepares one idempotently named completed summary when its Cindy timer ends or the operator explicitly stops the workout. It does not save interrupted sessions. Connected workout publishing requires explicit, verified non-admin event and summary origins. Mocked tests cover delivery and Cindy PT ingestion. See [webcam workflow](docs/webcam-three-exercise.md).

### Start a webcam workout from the dashboard

Start TinyIoT, one FastAPI worker, and the React dashboard as above on the **same Windows computer**. Install `opencv-python` and `mediapipe` in `.venv-webcam`, and place the MediaPipe model at `models/pose_landmarker_lite.task`. In `backend/.env`, set `CINDYSENSE_WEBCAM_PYTHON` to that environment's absolute `python.exe` path and set `CINDYSENSE_EVENT_ORIGIN` and `CINDYSENSE_SUMMARY_ORIGIN` to verified non-admin originators with the existing repetition and summary container permissions. The summary origin may be omitted if the event origin has both permissions. These values stay in FastAPI; React receives no oneM2M credentials. The API refuses Start until an authorized publisher is configured.

Select **Webcam live** and click **Start Workout**. FastAPI launches the existing detector once, and the dashboard shows its actual annotated JPEG frames, camera status, remaining time, current-round repetitions, and completed rounds. Refreshing the page reconnects to the same worker. **Stop Workout** asks the runner to perform its normal explicit-stop completion and save an idempotently named `WorkoutSummary` with the worker's session ID. Natural 20-minute expiry follows the same completion path. The stored repetition snapshot remains sourced from TinyIoT and can lag the worker's immediately displayed counts while publication is pending. A camera failure or process exit is shown as an error, without saving an interrupted workout.

Restart FastAPI after changing its code or `backend/.env`. The detector has exited when the worker state becomes `stopped`; `stopping` means it is still releasing the camera and attempting publication. A stopped workout may still have locally queued events or a summary: check `summaryPublication`, `eventJournalPending`, and `summaryPending` before treating it as stored. If stopping takes over 15 seconds, the dashboard shows the worker PID. Retry Stop or inspect `data/webcam-worker-ipc/worker.log` and that specific PID; do not terminate unrelated Python processes. If the preview reports missing frames, check the same worker status and Windows camera access. The worker status file lets a restarted single FastAPI process reconnect to an active detector and send it a stop signal.

The control and preview API only accepts requests from the local machine. Run FastAPI with one worker; do not use multiple worker processes for webcam control. A cross-process camera lock also prevents a manually started runner from owning the same camera. Browser camera permission is not involved because OpenCV opens the camera in the local Python worker; allow camera access to desktop apps in Windows settings. Live camera and oneM2M notification delivery require a real hardware session to verify. No historical summary is created by starting the dashboard.

Cindy PT also offers a rule-based seven-day schedule preview using the stored goal, completed workout history, chosen training days, experience level, and browser timezone. Preview reads TinyIoT only. A separately confirmed save writes named content instances to the existing `CindyPT_AE/TrainingPlans`, `WorkoutSchedule`, and `RecoveryRecommendations` containers. No live plan write has been attempted. See [API contract](docs/api.md).

### Local webcam Voice Coach

Run the webcam runner in Cindy mode and open the dashboard on the **same computer** at `http://localhost:5173`. The runner exposes confirmed repetition and workout progression events on loopback port 8765; the browser speaks them with the Web Speech API. The dashboard's Voice Coach controls set ON/OFF, volume, and speed, saved in browser storage. Speech is active only for a running webcam Cindy session. Browser audio may require one click on the page before it starts. The local stream works even if TinyIoT is disconnected; the existing TinyIoT publisher continues independently. Use `--voice-events-port 0` to disable the local stream, or set another port and update the dashboard EventSource URL if 8765 is occupied. A dashboard on another computer cannot receive this loopback stream.

```sh
python tools/webcam_squat.py --exercise cindy
```

For connected TinyIoT publishing, use the verified non-admin origin flags in the [end-to-end demo instructions](docs/hackathon-evidence/demo-instructions.md).

To check with a camera and speaker, start the runner and dashboard, click the page once, then complete five pull-ups, ten push-ups, and fifteen squats. Check that only confirmed reps speak, transitions and the new round speak once, Voice Coach OFF stops speech, and `R` clears it. Use a short `--duration-minutes` value to check completion; the 10/5/1 minute reminders are emitted only when that duration reaches each threshold. The runner's existing `S` and `Q` controls still stop or discard the session. The browser voice controls currently apply to the local webcam source; ESP32 live and dashboard demo modes remain visual only.

For push-up and voice timing checks, perform ten slow and ten fast full push-ups. The runner logs `[PUSHUP]` state changes with frame gaps, pose processing time, angle, shoulder position, and rejection reason. Its CSV also records `frameGapMs` and `poseMs` on every frame. `[VOICE]` logs confirmation to local publication; browser DevTools logs `[CindySense voice latency]` with local publication to dashboard receipt and receipt to speech start. Compare those values to find whether delay is in detection, local delivery, or browser speech. The browser's `onstart` timestamp is an approximation of audible onset; verify the speaker by ear.

## Checks

```sh
python -m unittest discover -s backend/tests
cd dashboard
node --test src/services/*.test.js
npm run build
npm run lint
```

For the tool tests in the existing Windows environments, run the webcam tests with `.venv-webcam` and the `httpx`-dependent reliability monitor tests with `backend/.venv`:

```powershell
$modules = @(Get-ChildItem tools/tests/test_*.py | Where-Object Name -ne 'test_monitor_cindy_reliability.py' | ForEach-Object { 'tools.tests.' + $_.BaseName })
& .\.venv-webcam\Scripts\python.exe -m unittest -q @modules
& .\backend\.venv\Scripts\python.exe -m unittest -q tools.tests.test_monitor_cindy_reliability
```

On Windows PowerShell, use `npm.cmd` for the dashboard commands when the `npm.ps1` shim is blocked. Tests use mocks and disposable local fixtures; they do not require a live TinyIoT server. No firmware build command is configured in this repository.


Read [architecture](docs/architecture.md), [API](docs/api.md), and [TinyIoT setup](docs/tinyiot-setup.md) before connecting devices or changing oneM2M resources. The [consolidation plan](docs/consolidation-plan.md) records the source comparison and team review steps. Detailed notes from the standalone dashboard repository are preserved as [historical dashboard notes](docs/dashboard-standalone-notes.md); use the commands above for this repository layout.

To capture labelled MPU6050 batches from an existing SensorData container, follow [sensor data collection](docs/sensor-data-collection.md).

