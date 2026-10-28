# Standalone dashboard notes (historical)

This document preserves the README from `Eppy-plaintext/cindysense-dashboard`
at commit `5e4ad6648213b73c43ce7d6d9763bdc506d84630`. It describes the
original standalone checkout and includes prototype observations, not current
team setup instructions. In the consolidated repository, run frontend
commands from `dashboard/` and backend commands from the repository root.
Use the [root README](../README.md) for current setup. The identities and
resource-access examples below must be revalidated before use on a shared
TinyIoT instance.

---

# CindySense

React/Vite demo dashboard plus a FastAPI bridge to TinyIoT. The dashboard reads stored TinyIoT snapshots through FastAPI; development simulation writes are opt-in and disabled by default. Its workout timer, repetitions, rounds, history examples, and training-plan examples remain simulated.

## Architecture

`React (:5173) → FastAPI (:8000) → TinyIoT (:3000/TinyIoT)`

TinyIoT runs in WSL Ubuntu. FastAPI can run either in that WSL distribution or in Windows if `http://localhost:3000/TinyIoT` is reachable from Windows.

## Start TinyIoT and verify access

Start the existing TinyIoT server in WSL using its own project instructions. The following read requests have been verified with HTTP 200 and `X-M2M-RVI: 4`:

- `CAdmin` reads `CindySense_AE/RepetitionCount` and its `/la` content instance.
- `CCindyPT` reads `CindyPT_AE/AIRecommendations` and its `/la` content instance.

These are currently verified read identities, **not** evidence of least-privilege access. In particular, `CAdmin` is an administrative identity. The backend does not modify ACP permissions; the disabled-by-default development endpoint can create content instances only after explicit configuration.

Example read-only verification:

```sh
curl -i -H 'X-M2M-Origin: CAdmin' -H 'X-M2M-RI: verify-1' -H 'X-M2M-RVI: 4' -H 'Accept: application/json' http://localhost:3000/TinyIoT/CindySense_AE/RepetitionCount/la
curl -i -H 'X-M2M-Origin: CCindyPT' -H 'X-M2M-RI: verify-2' -H 'X-M2M-RVI: 4' -H 'Accept: application/json' http://localhost:3000/TinyIoT/CindyPT_AE/AIRecommendations/la
```

The backend verifies each container before reading `/la` (latest content instance). The example environment file supplies these origins and the verified paths; use a different server-side origin only after verifying its read access. No administrative origin or credential belongs in the React app.

## Run FastAPI (WSL Ubuntu)

From the project root, with Python 3.10+ installed:

```sh
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
# Confirm backend/.env matches the TinyIoT instance reachable from WSL.
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

## Run FastAPI (Windows PowerShell)

From the project root:

```powershell
py -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
# Confirm backend/.env matches the TinyIoT instance reachable from Windows.
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

`backend/.env` is ignored by Git. It contains `TINYIOT_WORKOUT_ORIGIN=CAdmin` and `TINYIOT_CINDYPT_ORIGIN=CCindyPT` for the verified read paths. Set `TINYIOT_BASE_URL` to an address reachable **from the FastAPI process**. The backend sends `X-M2M-Origin`, a unique `X-M2M-RI`, and `X-M2M-RVI: 4` on each oneM2M request. Keep this file server-side; never copy it into React or commit it.

## API

- `GET /api/health` — backend availability and TinyIoT reachability/resource access separately. Backend health remains available even when TinyIoT is down or access is denied.
- `GET /api/workout/live` — returns `latest_repetition_content` from the verified `/la` instance, with its source and timestamp. The current stored value is `11`; it is **not** identified as 11 pull-ups, 11 current-session reps, or a round score. This is a point-in-time read, not a real-time update stream.
- `GET /api/workout/history` — `available: false` and an empty `sessions` list until a real history resource is integrated.
- `GET /api/cindypt/recommendations` — returns the verified `/la` recommendation object, including its stored `roundsCompleted`, `durationSeconds`, `recommendation`, and `source` fields.

Forbidden resources return 403, missing resources 404, unreachable TinyIoT 503, and malformed upstream content 502. API documentation is at `http://localhost:8000/docs`.

## Run all three services

1. Start the existing TinyIoT server in WSL Ubuntu on port 3000. Confirm `http://localhost:3000/TinyIoT` is reachable from the FastAPI process.
2. Start FastAPI on port 8000 using the WSL or Windows instructions above. Check `http://localhost:8000/api/health`; `backend.available` and `tinyiot.reachable` are separate indicators. The local [backend environment example](../backend/.env.example) contains the verified read origins and resource paths.
3. Start React/Vite on port 5173 from the project root:

```sh
npm install
npm run dev
```

The browser API client uses `VITE_API_BASE_URL`, falling back to `http://localhost:8000`. To override it locally, copy the root [.env.example](../dashboard/.env.example) to `.env.local` and edit **only** the browser-to-FastAPI URL. Vite variables are exposed in browser code: never add oneM2M origins or server-side credentials there.

Open `http://localhost:5173/`. The connection indicator distinguishes backend availability from TinyIoT reachability. Dashboard and Cindy PT poll their stored-content endpoints every 5 seconds while their views are active; health polls every 5 seconds. Requests do not overlap, and polling stops when a view is left. This is periodic retrieval of the latest saved content, **not** real-time sensor streaming. The stored repetition value is unclassified and never changes the demo workout counters. Stored advice on Cindy PT is visually separate from the simulated plan. History explicitly reports that no backend sessions are available, while retaining labeled demo examples.

To run checks, use `npm run build` and `npm run lint`. There is no configured frontend test script. For backend tests, run `python -m unittest discover -s backend/tests` with the WSL virtual environment activated, or `backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests` on Windows.

## Structured repetition events (development preparation)

Reads remain active, while development simulation writes are disabled by default. The existing `CindySense_AE/RepetitionCount` container may hold legacy numeric content such as `11`. The API classifies it as `legacy_unclassified`, never as an exercise or current-session count. A structured content instance instead has this JSON object:

```json
{
  "eventId": "81f6e2b6-866f-4387-a1fa-31e54c5365a0",
  "sessionId": "session-20261027-01",
  "exercise": "pullup",
  "repetitions": 3,
  "round": 1,
  "timestamp": "2026-10-27T12:00:00Z",
  "source": "simulator"
}
```

`exercise` is exactly `pullup`, `pushup`, or `squat`. `repetitions` is a nonnegative **absolute count for that exercise in the specified round**, not an increment. `round` starts at 1. `timestamp` must be an ISO 8601 date-time with a timezone. `eventId` is a UUID unique to the logical event; `sessionId` is 1-100 ASCII letters, digits, underscores or hyphens and starts with a letter or digit. Unknown fields are rejected. The development endpoint accepts only `source: "simulator"`; `mpu6050` and `manual` are reserved for future trusted ingestion routes. Simulated events are not sensor measurements.

`GET /api/workout/live` retains `latest_repetition_content` for the current React client and adds `content_kind` (`structured_event` or `legacy_unclassified`), `structured_event` (validated object or `null`), and `legacy_content` (numeric value or `null`). Invalid structured content returns 502. The dashboard keeps Demo Mode and does not update its exercise counters from these snapshots.

For future session reconstruction, deduplicate by `eventId`, reject the same ID with different content, and order by `timestamp` with `eventId` as a deterministic tie-break. Because counts are absolute per exercise/round, do not add event counts together. The `/la` snapshot alone cannot reconstruct complete session state after missed events; a future history API must enumerate instances or use a durable event store. The existing history endpoint remains unavailable.

### Opt-in local simulation writes

`POST /api/dev/workout/events` accepts the event JSON above only from a loopback client. It requires `CINDYSENSE_ENABLE_SIMULATION_WRITES=true`, a private backend-only `CINDYSENSE_SIMULATION_TOKEN` of at least 32 characters, and a separately configured `TINYIOT_SIMULATION_ORIGIN` with verified write access. Supply the token in `X-Dev-Simulation-Token`. The [backend environment example](../backend/.env.example) leaves writes disabled and both secrets blank. Do not reuse the administrative read identity just for convenience, broaden ACPs, or put any token or oneM2M origin in the React/Vite environment. Browser CORS permits only GET.

The backend proposes the deterministic oneM2M content-instance name `sim-<eventId without hyphens>`. It checks for that child before POST, verifies stored content after creation, treats an exact retry as a duplicate, and returns 409 if the same ID has different content. This is idempotent if TinyIoT enforces unique child names. The behavior is tested with a mock server but **has not been verified against the live TinyIoT server**. Do not enable writes or publish events until the separate write identity and resource-name behavior have been explicitly verified in a controlled development environment. No simulation event was published as part of this change.

### Proposed one-event authorization test — approval required

Read-only inspection on 2026-10-27 found that `TinyIoT/CindySense_AE/RepetitionCount` has no explicit `acpi` and inherits the `TinyIoT/CindySense_ACP` rules referenced by its parent AE (`ri: 1-20261026T160317,2210029`). That ACP currently grants `CAdmin` `acop: 63`, and `CCindyPT` and `CDashboard` `acop: 2`; it does not mention `CSimulator`. `CSimulator` receives HTTP 403 / `X-M2M-RSC: 4103` when retrieving the container. Do **not** broaden the shared AE ACP merely to run this test.

The proposed narrower change, **not applied**, is:

1. Create `TinyIoT/CindySense_RepetitionSimulation_ACP` with `pv.acr: [{"acor":["CSimulator"],"acop":3}]` (CREATE + RETRIEVE) and `pvs.acr: [{"acor":["CAdmin"],"acop":63}]` (policy management retained by CAdmin). No simulator UPDATE, DELETE, DISCOVER or NOTIFY permission.
2. On `TinyIoT/CindySense_AE/RepetitionCount` only, set `acpi` to `["1-20261026T160317,2210029", "<new ACP ri>"]`. Preserve the existing ACP reference so all current grants remain. Do not change the AE, existing ACP, content instances or subscriptions. Re-read both resources and confirm the current values before applying any update; this proposal depends on the observed server state and TinyIoT's support for container-specific multiple ACP references.

The intended oneM2M changes, if approved later, are `POST /TinyIoT` (resource type 1) with `{"m2m:acp":{"rn":"CindySense_RepetitionSimulation_ACP","pv":{"acr":[{"acor":["CSimulator"],"acop":3}]},"pvs":{"acr":[{"acor":["CAdmin"],"acop":63}]}}}` followed by `PUT /TinyIoT/CindySense_AE/RepetitionCount` with `{"m2m:cnt":{"acpi":["1-20261026T160317,2210029","<ri returned by ACP creation>"]}}`. Both would use `X-M2M-Origin: CAdmin`, a fresh `X-M2M-RI`, and `X-M2M-RVI: 4`. They are **proposals, not commands executed**. The container-specific multi-ACP behavior must be verified before any simulator event is posted.

Stage A is authorization preparation only. Read-only discovery of the current TinyIoT instance found 4 ACPs, 3 AEs and 21 containers, with no resource carrying multiple explicit `acpi` values. Before an approved Stage A execution, re-read the AE, container and `CindySense_ACP`; confirm the container still has **no explicit `acpi`**, its baseline resource ID is `3-20261026T145138,5460005` and `lt` is `20261026T145138,546`, the AE still references `1-20261026T160317,2210029`, and the proposed new ACP name is still absent. Stop on any drift. A local mock or oneM2M specification cannot prove this TinyIoT deployment's multi-ACP implementation.

After an approved ACP creation, read it back and verify its exact `pv` and `pvs` and returned resource ID. After an approved container update, read back `acpi` and check: `CAdmin`, `CCindyPT` and `CDashboard` still retrieve the container and `/la`; `CSimulator` retrieves those two but remains forbidden on `CindySense_AE` and a sibling such as `CindySense_AE/SensorData`; `CSimulator` cannot retrieve or administer the new ACP. The ACP readback should show only CREATE (1) and RETRIEVE (2) for `CSimulator`; no live CREATE, UPDATE or DELETE test is part of Stage A. If any check fails, stop before Stage B and roll back.

Rollback plan: keep a private pre-change snapshot of the container's representation and its **absent** `acpi`. If ACP creation succeeds but container update fails, confirm the ACP is unreferenced before removing only that newly created ACP. If the container update succeeds but verification fails, use `CAdmin` to restore the original **absence** of `acpi` with `PUT {"m2m:cnt":{"acpi":null}}`; read the container back, verify baseline access and `CSimulator` denial, restart and verify persistence if appropriate, then remove only the unreferenced new ACP. This exact removal worked on the isolated SQLite build described below. The production PostgreSQL deployment has not undergone the update/rollback and must still be checked in place if Stage A is later approved. If restoration fails, stop and report rollback as incomplete; do not attempt an event write.

### Isolated TinyIoT ACP compatibility check (2026-10-27)

The running port-3000 binary uses PostgreSQL database `tinydb`. A separate source copy was compiled with `DB_TYPE DB_SQLITE`, verified to have no `libpq` dependency, and started on port 3100 from a temporary directory with its own relative `data.db`. Before testing, discovery on port 3100 returned only `TinyIoT/defaultACP`. MQTT and CoAP were disabled in that build. The temporary server was stopped and its source/database copy removed after the test; port 3000 and its storage were never mutation targets.

On port 3100, a test AE and two child containers inherited a base ACP giving `CAdmin` and `COriginalWriter` full privileges and `CReaderPT`/`CReaderDashboard` RETRIEVE. `CSimulatorTest` initially received HTTP 403 / oneM2M `4103` on all three. A second ACP gave only `CSimulatorTest` `pv.acr acop: 3` and `CAdmin` `pvs.acr acop: 63`. The server accepted `acpi: [<base ACP ri>, <simulation ACP ri>]` on only the target container with HTTP 200 / `2004`, and returned both IDs on readback. All original origins retained HTTP 200 / `2000` retrieval. `CSimulatorTest` retrieved only the target container and its child (HTTP 200 / `2000`), while the parent AE, sibling container and simulation ACP stayed forbidden (HTTP 403 / `4103`). It created one disposable content instance (HTTP 201 / `2001`); UPDATE of the target container and DELETE of that child both returned HTTP 403 / `4103`.

For rollback, `PUT {"m2m:cnt":{"acpi":null}}` returned HTTP 200 / `2004`; readback omitted `acpi`. The original origins retained retrieval and `CSimulatorTest` was denied again. After restarting the disposable server, SQLite still showed no explicit `acpi`, and the same permissions held. The production PostgreSQL source has a NULL-handling path for updated attributes, but its persistence behavior was not changed or empirically tested. These results support a guarded Stage A attempt with fresh preflight reads and immediate post-update verification, **not** permission to apply it or publish an event without explicit approval.

The local backend environment file has a private 32-byte random simulation token and `TINYIOT_SIMULATION_ORIGIN=CSimulator`; `CINDYSENSE_ENABLE_SIMULATION_WRITES=false` remains the default. Never display the token. After explicit approval of **both** the ACP change and the one-event write, restart the local FastAPI process with `CINDYSENSE_ENABLE_SIMULATION_WRITES=true` set only in that process environment. Then, from a separate PowerShell terminal in the project root, this command reads the backend-only token without printing it and posts one newly identified, timezone-aware simulator event:

```powershell
$tokenLine = Get-Content backend/.env | Where-Object { $_ -match '^CINDYSENSE_SIMULATION_TOKEN=' } | Select-Object -First 1
$simulationToken = ($tokenLine -split '=', 2)[1]
$eventBody = @{
  eventId = [guid]::NewGuid().ToString()
  sessionId = 'demo-session-001'
  exercise = 'pullup'
  repetitions = 1
  round = 1
  timestamp = (Get-Date).ToString('o')
  source = 'simulator'
} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/dev/workout/events' -Headers @{ 'X-Dev-Simulation-Token' = $simulationToken } -ContentType 'application/json' -Body $eventBody
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/api/workout/live'
```

After the test, stop that process and restart FastAPI without the write-enable override. `GET /api/workout/live` reads only `/la`; another content instance could become latest before verification. The backend itself checks the deterministic child resource after POST, but full session reconstruction remains unsupported.
