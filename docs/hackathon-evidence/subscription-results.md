# Subscription notification results

## Historical replay attempt (2026-11-12)

No historical summary notification was submitted. TinyIoT summary listing and root retrieval timed out with no HTTP response or X-M2M-RSC, so the callback's required stored-CIN validation could not run. FastAPI metrics remained at zero validated, duplicate, and rejected callbacks with no coaching jobs. See [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md).

## Phase 5 live verification (2026-11-12)

After the approved ACP migration, `RepCount_Sub` and `Summary_Sub` each returned GET **HTTP 200 / X-M2M-RSC 2000** to CAdmin, CCindyPT, and CDashboard, and **403 / 4103** to CWebcam. Each retained its original RI, one callback URI, `enc.net=[3]`, and `nct=1`. The full callback URI values matched the configured backend URL in memory but were never written into evidence or logs. The existing subscription resource URLs and criteria were **not changed**.

FastAPI started on port 8000 with HTTP access logs disabled. Windows GET `/api/health` returned HTTP **200** with backend available and TinyIoT reachable/resource verified. WSL GET of the backend's address returned HTTP **200**. A disposable CNT and SUB using the configured callback endpoint were created: CNT **201 / 2001**, SUB **201 / 2001** after TinyIoT's verification request, SUB readback **200 / 2000**, unchanged callback URI and `net=[3]`. The disposable CNT DELETE was **200 / 2002**; CNT and SUB GETs were **404 / 4004** afterward. This is a live callback **verification-request** test, not a completed-workout notification.

At the most recent read, `GET /api/integration/metrics` reported `validatedCallbacks=0`, `duplicateCallbacks=0`, `rejectedCallbacks=0`, and an empty coaching job map. A real completed WorkoutSummary notification and automatic recommendation are **NOT VERIFIED** until the physical webcam workout runs. Do not count the verification request as a workout callback.

---

Existing `Summary_Sub` and `RepCount_Sub` are reused by the backend. A callback requires a configured token, valid oneM2M headers and subscription reference, and a CIN whose identity and content match an authenticated TinyIoT readback. Repeated validated summary callbacks enqueue the same durable job once. The notification responds before CindyPT processing.

Earlier live subscription delivery and duplicate suppression are recorded in [onem2m-feature-audit.md](../onem2m-feature-audit.md). Phase 4 tested automatic recommendation notification validation with mocks; Phase 5 verified the live callback handshake above. **New automatic recommendation delivery is NOT VERIFIED live.** The Phase 5 production migration changed only the two subscription `acpi` assignments, not their callback URLs or event criteria.

Presentation evidence should include a redacted TinyIoT CIN POST response (HTTP 201/RSC 2001), callback acknowledgment (HTTP 200/RSC 2000), `GET /api/integration/metrics` with coaching job counts, and the stored recommendation CIN readback. Never show notification token or callback URI.
