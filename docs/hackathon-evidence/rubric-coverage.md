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

Suggested presentation captures: redacted oneM2M tree and ACP assignments; an authorized and denied request pair showing HTTP and RSC; group `mid` readback and `/fopt` aggregate; type/label discovery URIs; repetition and summary CINs; callback metrics; recommendation CIN and Cindy PT page; webcam counter and voice demonstration. Capture these during a real connected rehearsal. Do not reuse mock output as live evidence.
