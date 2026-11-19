# Final live integration audit — 2026-11-19

This audit used read-only GETs against the running CindySense deployment, the existing test suites, the offline MCP fixture, and the browser at `http://localhost:5173/`. It did not start a workout, restart a service, publish a content instance, change an ACP, or create a resource. The working tree was already modified and contained untracked files; they were preserved.

## 1. System health and service status

| Check | Current result | Evidence label |
| --- | --- | --- |
| TinyIoT `GET http://127.0.0.1:3001/TinyIoT` as `CAdmin` | HTTP 200, oneM2M RSC 2000, RVI 4, `tinyIoT v0.1`. | **LIVE VERIFIED** |
| FastAPI `GET http://127.0.0.1:8000/api/health` | HTTP 200; backend available, TinyIoT reachable, repetition resource verified, subscriptions configured. | **LIVE VERIFIED** |
| Dashboard `http://localhost:5173/` | HTTP 200 and browser rendered backend Online / TinyIoT Connected with current stored workout data. | **LIVE VERIFIED** |
| Dashboard `http://127.0.0.1:5173/` | Connection refused; Vite listens on IPv6 loopback `::1`. `http://[::1]:5173/` loads but is outside the backend's CORS origin list, so use `localhost`. | **LIVE VERIFIED** |
| PostgreSQL | WSL `pg_isready -h 127.0.0.1 -p 5432` reported accepting connections; CSE read queries succeeded. Authenticated SQL query was not performed. | **LIVE VERIFIED** for listener, **NOT VERIFIED** for direct SQL login |
| WSL and port forwarding | Ubuntu-22.04 running at `172.31.28.214`; Windows port proxy `0.0.0.0:3001 → 172.31.28.214:3001`. No network change made. | **LIVE VERIFIED** |

The prior TinyIoT timeout and FastAPI unavailability are resolved in the currently running environment. Browser URL matters: use `http://localhost:5173/`, whose origin is allowed by [FastAPI CORS settings](../../backend/main.py).

## 2. Existing oneM2M resources

The paths below were verified by direct GET using the expected application origin, except ACPs and MECBridge discovery, which used `CAdmin`. Every listed resource returned HTTP 200 / RSC 2000. `acpi` entries are resource IDs, not credentials. Resource types: AE 2, CNT 3, GRP 9, SUB 23, TS 29, ACP 1.

| Resource | Type | Relevant live attributes | Read origin | Evidence |
| --- | --- | --- | --- | --- |
| `CindySense_AE` | 2 | `aei=CAdmin`; parent ACP assigned | `CDashboard` | **LIVE VERIFIED** |
| `Webcam_AE` | 2 | `aei=CWebcam`; no explicit `acpi` | `CWebcam` | **LIVE VERIFIED** |
| `CindyPT_AE` | 2 | `aei=CCindyPT`; CindyPT ACP assigned | `CCindyPT` | **LIVE VERIFIED** |
| `Dashboard_AE` | 2 | `aei=CDashboard`; Dashboard ACP assigned | `CDashboard` | **LIVE VERIFIED** |
| `MECBridge_AE` | 2 | `aei=CMECBridge`; MECBridge ACP assigned | `CMECBridge` | **LIVE VERIFIED** |
| `CindySense_AE/RepetitionCount` | 3 | `cni=913`, `mni=1000`, `mbs=524288`; publisher ACP assigned | `CDashboard` | **LIVE VERIFIED** |
| `CindySense_AE/WorkoutSessions` | 3 | `cni=1`, `mni=100` | `CDashboard` | **LIVE VERIFIED** |
| `CindySense_AE/WorkoutSummary` | 3 | `cni=18`, `mni=100`; publisher ACP assigned | `CDashboard` | **LIVE VERIFIED** |
| `CindySense_AE/WorkoutMonitoring_GRP` | 9 | Three CNT member IDs | `CDashboard` | **LIVE VERIFIED** |
| `CindySense_AE/WorkoutPerformance_TS` | 29 | `cni=1`, `mni=100`, `mbs=262144` | `CDashboard` | **LIVE VERIFIED** |
| Stored TSI under `WorkoutPerformance_TS` | 30 | One child reference and one retrieved instance; content has `workoutId`, `reps`, `rounds`, duration, source timestamp | `CDashboard` through history API | **LIVE VERIFIED** |
| `CindyPT_AE/FitnessGoals` | 3 | `cni=1`; current goal is visibly marked integration test data | `CCindyPT` | **LIVE VERIFIED** |
| `CindyPT_AE/AIRecommendations` | 3 | `cni=15`; saved recommendation matches selected workout | `CDashboard` | **LIVE VERIFIED** |
| `MECBridge_AE/WLANInformation` | 3 | `cni=2` | `CMECBridge` | **LIVE VERIFIED** for stored resource; live MEC API result **NOT VERIFIED** |
| `RepetitionCount/RepCount_Sub`, `WorkoutSummary/Summary_Sub` | 23 | Both `enc.net=[3]`, `nct=1`, explicit guard ACP | `CCindyPT` | **LIVE VERIFIED** configuration |
| `CindySense_ACP`, `CindyPT_ACP`, `Dashboard_ACP`, `MECBridge_ACP`, `CindySense_AE/CindySense_WebcamPublisher_ACP` | 1 | Definitions and assignment IDs read; see section 4 | `CAdmin` | **LIVE VERIFIED** |

The publisher ACP is a child of `CindySense_AE`; its root-level path returns HTTP 404 / RSC 4004. No resource was created to fill a missing path.

Observed resource IDs anchor the verified paths: `RepetitionCount=3-20261026T145138,5460005`, `WorkoutSummary=3-20261026T145138,5610007`, `WorkoutSessions=3-20261026T145138,5540006`, `WorkoutMonitoring_GRP=9-20261111T032431,2490006`, and `WorkoutPerformance_TS=29-20261111T113424,1571209`. The publisher ACP ID is `1-20261112T101707,2420075`; both workout CNT `acpi` values matched it. Both SUB `acpi` values matched `CindySense_ACP=1-20261026T160317,2210029`. The resource IDs were read back during this audit rather than inferred from names.

## 3. Discovery, Group, TimeSeries, and subscriptions

- **LIVE VERIFIED:** `/api/onem2m/discovery` returned 11 CindySense CNT references. `/api/onem2m/discovery/status` returned `available=true` and `discovered` for AEs, CindySense CNTs, and repetition SUBs. The CSE's unfiltered root discovery returned 151 CIN references; type-filtered queries are needed for other resource classes.
- **LIVE VERIFIED:** `/api/onem2m/group/workout-monitoring` returned `overall_status=success`, member RSCs `2000,2000,2000`, and representations of `RepetitionCount`, `WorkoutSummary`, and `WorkoutSessions`. Group member IDs matched the direct CNT readbacks.
- **LIVE VERIFIED:** `/api/onem2m/workout-timeseries` returned HTTP 200, `count=1`, and a retrieved TSI. `/api/workout/history` returned HTTP 200 with 24 sessions, two comparable 20-minute workouts, and 913 retained repetition instances. The running FastAPI therefore serves the corrected TimeSeries history read path; no restart is currently required.
- **LIVE VERIFIED:** The subscription monitor reports `verified`, with repetition and summary subscriptions `healthy`. It reports two repetition subscriptions and one summary subscription. Recovery is disabled in the running configuration. Its current-process counters show zero received/accepted/validated callbacks and no last callback, so actual notification delivery during this audit is **NOT VERIFIED**. Existing callback tests are **LOCALLY TESTED**.

## 4. ACP and application separation

| Policy | Live `pv` permissions (`acop`) | Assignment / effect |
| --- | --- | --- |
| `CindySense_ACP` | `CAdmin=63`; `CCindyPT,CDashboard=2` | CindySense AE and both protected SUBs. |
| `CindySense_WebcamPublisher_ACP` | `CAdmin=63`; `CWebcam=3`; `CCindyPT,CDashboard=2` | Repetition and summary CNTs only. |
| `CindyPT_ACP` | `CCindyPT=63`; `CAdmin,CDashboard=2` | CindyPT AE; recommendation CNT inherits. |
| `Dashboard_ACP` | `CDashboard=63`; `CAdmin=2` | Dashboard AE. |
| `MECBridge_ACP` | `CAdmin=63`; `CMECBridge=3` | MECBridge AE. |

**LIVE VERIFIED read-only allow/deny:** `CWebcam`, `CCindyPT`, and `CDashboard` retrieved both workout CNTs (200/2000); `CMECBridge` and `CIntruder` were denied (403/4103). `CWebcam` was denied both SUB readbacks (403/4103), while `CCindyPT` and `CDashboard` could retrieve them. Only `CMECBridge` among the tested non-admin origins could retrieve `MECBridge_AE`; only `CDashboard` could retrieve `Dashboard_AE`. This is current read enforcement. Write permission evidence remains **LOCALLY TESTED** in isolated fixtures and historical disposable CSE probes; no production POST, PUT, or DELETE was attempted. TinyIoT's create privilege is not limited to CIN creation. The configured transport is HTTP; HTTPS/TLS remains **NOT VERIFIED**.

## 5. CindySense-to-CindyPT integration

- **LIVE VERIFIED:** `/api/workout/snapshot` returned available TinyIoT data. The dashboard showed a completed 20-minute webcam session with confirmed repetitions and five complete Cindy rounds. `/api/workout/webcam-worker` reported `stopped`, `summaryPublication=delivered`, zero pending/failed journal events, zero pending/failed summaries, and no journal error. No camera was run in this audit.
- **LIVE VERIFIED:** `/api/cindypt/goal`, `/api/cindypt/progress`, `/api/cindypt/recommendations/current`, and `/api/cindypt/recommendations` returned HTTP 200. Progress selection was `sufficient`; the coaching workout ID equaled both the current rule-based preview and saved recommendation source workout IDs. The Cindy PT page displayed the verified workout and stored advice. The current goal and saved advice are visibly labeled **TEST DATA**.
- **LOCALLY TESTED:** Backend and tools fixtures cover webcam publication contracts, journaling, retry/recovery, workout summaries, subscription handling, CindyPT selection, and dashboard services. This audit did not validate physical repetition accuracy or a new end-to-end notification.
- **LIVE VERIFIED browser defect and fix:** Older summary-only rows showed `Invalid Date` because TinyIoT `YYYYMMDDTHHMMSS,mmm` timestamps were passed directly to JavaScript `Date`. [historyDisplay.js](../../dashboard/src/services/historyDisplay.js) now parses that format and uses `Date unknown` for malformed values; [StoredHistoryPage.jsx](../../dashboard/src/components/StoredHistoryPage.jsx) uses it for chart and table labels. A focused test and a browser check found zero remaining `Invalid Date` labels.

The dashboard tries `http://127.0.0.1:8765/events` for an optional local event stream; that connection was refused in this audit. Stored data and backend status still displayed. Do not present the optional stream as live delivery until the workout publisher is explicitly started and it connects.

## 6. MCP workflow

`.\.venv\Scripts\python.exe -m tools.mcp_workflow_demo` exited successfully. It used two actual in-process MCP servers and eight ordered tool-discovery/execution steps: oneM2M summary, goal, evidence, and publication; MEC discovery and WLAN read. SQLite state persisted as `completed`; after a new workflow object opened the same database, a duplicate run did not publish again (`publicationCount=1`, `duplicatePrevented=true`). Recommendation logic was deterministic. All oneM2M and MEC service data in this demo was **MOCKED**; MCP transport and SQLite persistence were **LOCALLY TESTED**. Live MEC calls and the historical HTTP 401 remain **NOT VERIFIED** in this audit; no authentication bypass was attempted.

## 7. Regression results and file changes

| Command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -q` | 294 tests, OK, no reported skips. |
| `.\.venv-webcam\Scripts\python.exe -m unittest discover -s tools/tests -q` | 167 tests, OK, no reported skips. |
| `cd dashboard; node --test src/services/*.test.js` | 48 passed, 0 failed, 0 skipped. |
| `cd dashboard; npm.cmd run build` | Passed. |
| `cd dashboard; npm.cmd run lint` | Passed. |
| `git diff --check` | Passed; line-ending notices only. |

Files changed in this audit: [historyDisplay.js](../../dashboard/src/services/historyDisplay.js), [StoredHistoryPage.jsx](../../dashboard/src/components/StoredHistoryPage.jsx), [historyDisplay.test.js](../../dashboard/src/services/historyDisplay.test.js), and this report. All other modified/untracked files were present before the audit. No confirmed backend regression required a code change.

## 8. Limitations and severity

1. **High for a live judge demonstration:** A new physical 20-minute workout, camera accuracy, and callback delivery were not exercised. The local event stream on port 8765 is stopped; start the workout only manually after checking camera framing and origin configuration. Do not claim a live callback based solely on healthy SUB configuration.
2. **Medium:** The current goal and saved recommendation are integration **TEST DATA**. For a personal demonstration, an authorized operator must decide whether to replace that goal and create new workout data; this audit made no such write.
3. **Medium:** The optional MEC API is not authenticated or verified here. The bridge AE and stored WLAN CNT exist, but the MCP demo's MEC responses are mocked.
4. **Medium for rubric criterion 5:** The installed TinyIoT requires a registered CindySense container definition before a genuine workout FlexContainer can be created. No such FCNT exists; see [prior capability assessment](./four-priority-rubric-20261119.md).
5. **Security limit:** HTTP is used locally. ACP create privilege is broader than CIN-only writes; CindyPT and Dashboard owner privileges are broad. No policy or transport change was authorized in this audit.
6. **Presentation limit:** `http://[::1]:5173/` is blocked by CORS and `127.0.0.1:5173` is not listening. Use `http://localhost:5173/`. The dashboard also contains clearly marked simulated insight/demo content; judges should be shown the stored-data sections.

## 9. Exact manual demonstration commands

Run from the repository root in PowerShell. All `curl.exe` examples are GET only and do not display credentials. Use a new `X-M2M-RI` for repeated oneM2M calls if needed.

```powershell
curl.exe -i http://127.0.0.1:8000/api/health
curl.exe -i -H "X-M2M-Origin: CAdmin" -H "X-M2M-RI: judge-root-1" -H "X-M2M-RVI: 4" http://127.0.0.1:3001/TinyIoT
curl.exe http://127.0.0.1:8000/api/onem2m/discovery/status
curl.exe http://127.0.0.1:8000/api/onem2m/group/workout-monitoring
curl.exe http://127.0.0.1:8000/api/onem2m/workout-timeseries
curl.exe http://127.0.0.1:8000/api/workout/history
curl.exe http://127.0.0.1:8000/api/cindypt/progress
curl.exe http://127.0.0.1:8000/api/cindypt/recommendations
.\.venv\Scripts\python.exe -m tools.mcp_workflow_demo
Start-Process 'http://localhost:5173/'
```

Expected observations: CSE RSC 2000; backend health resource verified; discovery available; Group three RSC 2000 members; TS one stored instance; history two comparable completed workouts; CindyPT coaching workout matches saved recommendation; offline MCP one publication and duplicate prevention. Show ACP read-only enforcement by GET of `CindySense_AE/RepetitionCount` as `CWebcam` (200/2000) and `CindySense_AE/RepetitionCount/RepCount_Sub` as `CWebcam` (403/4103), using the same headers as above with a distinct RI. Never print the SUB body or callback URI. Open the dashboard at `localhost`, then navigate to Workout history and Cindy PT. Do not click Start Workout as part of the read-only demonstration.

## 10. Final readiness assessment

The existing read path is **ready for a judge demonstration** of live oneM2M discovery, Group fan-out, stored TimeSeries, ACP read enforcement, workout history, and matching CindyPT advice. The offline MCP demonstration is reproducible and must be labeled **MOCKED** for external data. A manual 20-minute workout can be attempted after an operator checks camera framing, publishing origins, and the local event stream; current service and journal health provide a sound preflight, but physical accuracy and callback delivery remain unverified. The repository changes passed regression checks and `git diff --check`; they are technically ready for review before a Git commit/push. This audit did not commit or push and cannot certify the pre-existing uncommitted changes beyond the tests listed above.
