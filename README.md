# CindySense

CindySense is a three-person university hackathon prototype for IoT workout tracking. An ESP32 with an MPU6050 sends sensor readings to TinyIoT (oneM2M). FastAPI reads stored oneM2M content and serves a React/Vite dashboard. A separate Cindy PT script produces rule-based recommendations.

**Current state:** the dashboard's timer, exercise counters, rounds, history examples, and training-plan examples are demo data. Its TinyIoT panels show stored snapshots through FastAPI, polled every five seconds; they are not a live sensor stream. The firmware sends sensor readings but does not yet implement the promised exercise recognition or repetition counting.

## Components

| Folder | Purpose |
| --- | --- |
| [backend](backend/) | FastAPI bridge, oneM2M client, tests, and separate Cindy PT script |
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

[cindypt.py](backend/cindypt.py) is copied unchanged from the WSL prototype. It is a **standalone script with a write side effect**: running it reads `CindySense_AE/WorkoutSummary/la` and posts a recommendation to `CindyPT_AE/AIRecommendations`. It is not imported by FastAPI and is not run by the instructions above. Its loopback TinyIoT address and required oneM2M resources need review before a teammate runs it.

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

