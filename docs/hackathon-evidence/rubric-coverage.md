# Rubric coverage

**Current availability (2026-11-12):** The CindySense TinyIoT endpoint on port 3001 remains stopped following repeated HTTP stalls. A pre-existing original-binary process on port 3000 is connected to production `tinydb` and was left untouched. Phase 8 found and fixed an additional isolated transaction inconsistency after PostgreSQL connection loss; the four-file [candidate patch](./tinyiot-phase8-candidate.patch) passed isolated fault and representative oneM2M tests and built reproducibly. The Phase 7 20-minute cadence run used the earlier three-file binary. Neither patch has been deployed or verified against production. Earlier live ACP/resource readbacks remain historical evidence; current production integration is blocked. See [tinyiot-http-reliability.md](./tinyiot-http-reliability.md), [tinyiot-phase6-isolated-results.json](./tinyiot-phase6-isolated-results.json), and [tinyiot-phase7-endurance-results.json](./tinyiot-phase7-endurance-results.json).

Historical coaching replay attempted on 2026-11-12 but stopped before any write because TinyIoT did not answer read-only requests. Stored workout identity and automatic coaching remain **NOT VERIFIED live**; see [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md). Earlier ACP and disposable callback evidence remains valid for the time it was collected.

TinyIoT subsequently recovered through a graceful one-process restart with database contents, ACPs, and subscriptions preserved. Four summaries were read back, but the newest completion's physical webcam provenance could not be established, and a prior recommendation already references its workout ID. Historical replay was therefore not sent; automatic coaching and display of new advice remain **NOT VERIFIED**. See [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md).

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Webcam Cindy, three exercises, 20-minute workout | Runner and model retained; OpenCV/MediaPipe load and camera 0 opens; no physical exercise session yet | Implemented; live workout NOT VERIFIED |
| Multiple AEs | Live CindySense, Webcam, Dashboard, and CindyPT resources and distinct origins checked during ACP migration | SUPPORTED for resource and permission interaction |
| Access control | Approved production publisher ACP applied; subscriptions explicitly guarded; live allow/deny GETs and disposable POSTs using installed ACP | SUPPORTED for verified paths; direct real workout POST pending |
| CSE discovery | Backend endpoint and dashboard, historical type/label probe | Historically SUPPORTED; current NOT VERIFIED |
| Group fan-out | Existing GRP and endpoint, historical three-member readback | Historically SUPPORTED; current NOT VERIFIED |
| SUB callback and readback | Existing production subscriptions preserved; isolated FastAPI received verification and CIN event with matching resource identities | SUPPORTED in isolation; real workout callback NOT VERIFIED |
| Automatic CindyPT coaching | Durable queue, rule-based engine, named CIN readback, mocked end-to-end integration test passed | Implemented; live recommendation NOT VERIFIED |
| TS/TSI | Historical disposable lifecycle; no production workout analytics | PARTIALLY SUPPORTED |
| Dashboard sync and voice | React dashboard loads real TinyIoT data; build/lint pass; voice event server waits for webcam runner | Implemented; new workout sync and physical voice demo NOT VERIFIED |
| ESP32/MPU6050 | Kept in repository, excluded from active claims | Future work |
| TinyIoT HTTP reliability | Phase 7 isolated binary survived failed COMMIT and ROLLBACK, client disconnect, database outage/recovery, and 20-minute 1200-CIN/600-GET cadence with stable worker/socket use. Phase 8 candidate additionally rejected a mid-transaction disconnect instead of falsely acknowledging a CIN, and passed isolated retention and protocol checks. Original production stall's exact mutex owner remains unknown. | STAGING VERIFIED by test scope; Phase 8 long run and production NOT VERIFIED |

## Phase 8B final safety-check status (2026-11-14)

The complete Phase 8 candidate passed a fresh isolated 20-minute cadence run using 1,200/1,200 CIN POSTs and 600/600 container GETs. It remained at 2 threads, 7 descriptors, zero CLOSE_WAIT, and 12,624–13,380 KiB RSS; median latency was 18.24 ms, p95 39.71 ms, and maximum 173.62 ms. The isolated database cleanup removed the disposable AE and all test CINs. A fresh restricted full `pg_dump -Fc` backup of production `tinydb` was restored into a temporary database; table counts, representative resource IDs, ACP assignments, and `RepCount_Sub`/`Summary_Sub` records matched, and the temporary database was deleted. The backup SHA-256 was recorded privately in the reliability report.

The patch applied cleanly and a same-path clean rebuild reproduced the candidate hash. The original executable remained preserved and unchanged. The webcam default is now aligned with port 3001 while retaining its environment override. No active port-3000 listener, portproxy rule, startup command, or runtime default was found; historical references are retained as evidence. The candidate is **READY FOR APPROVAL**, with deployment and production restart still intentionally pending authorization.

The follow-up configuration audit found no remaining active port-3000 runtime default. Remaining port-3000 references are mock test inputs or historical evidence. Focused mocked validation passed **31/31** webcam/API/notification tests and **23/23** sensor-collector tests. The live repetition callback is named `cindysense-fastapi-repetitions` and the live summary callback is `Summary_Sub`; both use the configured port-8000 gateway and `net=3`. No subscription or oneM2M resource was changed.

## Phase 9B.1 fatigue-history retrieval status (2026-11-14)

Implemented a separate read-only history service in [backend/fatigue_history.py](../../backend/fatigue_history.py). The service independently validates paginated RepetitionCount CINs, records exclusion reasons, groups events by session, supports explicit session selection, and defaults to the latest session with sufficient pull-up, push-up, and squat history. Existing [backend/workout_snapshot.py](../../backend/workout_snapshot.py) counting behavior is unchanged.

Live read-only evidence: 292 retained/retrieved CIN references, 282 accepted webcam events, and 10 exclusions (8 diagnostic sessions, 2 synthetic records). The latest suitable session and the seven-round historical session were both analyzable without merging sessions. Focused tests passed 14/14; backend regression tests passed 51/51. The already-running FastAPI process still serves the old cache-based endpoint and must be restarted under a separately approved maintenance action; TinyIoT was not restarted or modified.

Suggested presentation captures: redacted oneM2M tree and ACP assignments; an authorized and denied request pair showing HTTP and RSC; group `mid` readback and `/fopt` aggregate; type/label discovery URIs; repetition and summary CINs; callback metrics; recommendation CIN and Cindy PT page; webcam counter and voice demonstration. Capture these during a real connected rehearsal. Do not reuse mock output as live evidence.
