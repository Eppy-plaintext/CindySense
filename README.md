# CindySense

CindySense is a three-person university hackathon prototype for IoT workout tracking. An ESP32 with an MPU6050 sends sensor readings to TinyIoT (oneM2M). FastAPI reads stored oneM2M content and serves a React/Vite dashboard. Its Cindy PT backend produces rule-based recommendations from completed workout summaries.

**Current state:** the dashboard's timer, exercise counters, rounds, and history examples include demo data. Its TinyIoT panels show stored snapshots through FastAPI, polled every five seconds; they are not a live sensor stream. The Cindy PT page reads stored goals, completed-workout progress, and rule-based recommendations through FastAPI. The firmware sends sensor readings but does not yet implement the promised exercise recognition or repetition counting.

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

Open `http://localhost:5173`. The browser calls FastAPI at `http://localhost:8000` by default. To change that address, copy [the dashboard example](dashboard/.env.example) to `dashboard/.env.local` and set `VITE_API_BASE_URL`. Vite environment values are visible in the browser, so never put credentials, tokens, or oneM2M originators there. The dashboard demo views work without TinyIoT; connected panels show unavailable status until FastAPI and TinyIoT are reachable.

### Firmware

Copy [config.example.h](firmware/config.example.h) to `firmware/config.h`, fill in your local WiFi settings, TinyIoT URL, and an authorized **device-specific** oneM2M originator, then open [cindysense.ino](firmware/cindysense.ino) in the Arduino IDE. Keep `config.h` local. The sketch's upload behavior is unchanged apart from reading these settings from the header. Do not use an administrative originator for a shared device.

### Cindy PT

[cindypt.py](backend/cindypt.py) provides rule-based coaching through FastAPI. Configure `TINYIOT_CINDYPT_ORIGIN` server-side for `CindySense_AE/WorkoutSummary`, `CindyPT_AE/FitnessGoals`, and `CindyPT_AE/AIRecommendations`. These containers existed on the previously inspected TinyIoT server. A verified completed-summary notification now queues automatic recommendation generation in a local SQLite job store; the worker reads the summary and current goal back from TinyIoT and saves idempotent rule-based advice. Manual generation remains available. Run a single FastAPI worker with a persistent writable `CINDYSENSE_COACHING_DB` location. The live automatic path has not yet been verified; see [hackathon evidence](docs/hackathon-evidence/capability-audit.md).

The webcam runner prepares one idempotently named completed summary when its Cindy timer ends or the operator explicitly stops the workout. It does not save interrupted sessions. Connected workout publishing requires explicit, verified non-admin event and summary origins; the earlier server's ACP did not grant `CWebcam` the required production access. Mocked tests cover delivery and Cindy PT ingestion. See [webcam workflow](docs/webcam-three-exercise.md).

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
npm run build
npm run lint
```

Run checks in a local copy with dependencies installed. The backend tests use mocks; they should not require a live TinyIoT server. No firmware build command is configured in this repository.


Read [architecture](docs/architecture.md), [API](docs/api.md), and [TinyIoT setup](docs/tinyiot-setup.md) before connecting devices or changing oneM2M resources. The [consolidation plan](docs/consolidation-plan.md) records the source comparison and team review steps. Detailed notes from the standalone dashboard repository are preserved as [historical dashboard notes](docs/dashboard-standalone-notes.md); use the commands above for this repository layout.

To capture labelled MPU6050 batches from an existing SensorData container, follow [sensor data collection](docs/sensor-data-collection.md).

