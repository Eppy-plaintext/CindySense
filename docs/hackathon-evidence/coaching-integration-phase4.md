# Automatic CindyPT integration verification — Phase 4

**Current readiness:** TinyIoT stalled again after recovery and was stopped gracefully to prevent further worker accumulation. FastAPI metrics and the dashboard still returned HTTP 200, but the CSE is unavailable, so subscription delivery and coaching readback are blocked. See [tinyiot-http-reliability.md](./tinyiot-http-reliability.md).

## Historical replay attempt (2026-11-12)

The requested replay of an existing completed workout stopped at read-only preflight because TinyIoT stopped responding to both Windows and WSL GET requests. No stored summary identity could be established, so no callback, queue job, recommendation write, or dashboard verification was attempted. See [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md). The earlier Phase 5 checks below were made before this CSE outage.

After a graceful restart, TinyIoT returned **200 / 2000** from both Windows and WSL, and all affected resource IDs, counts, ACP assignments, and subscription settings were preserved. The latest summary CIN read back as `final-summary-097c0172` with a completed webcam claim, but lacked the current runner's `completionReason`; its test supplied counts and absence of corroborating repetition events in the bounded 150-CIN read did not establish a genuine physical workout. An existing rule-based recommendation references this workout ID, while the durable job table is empty. To prevent a potentially duplicate or misleading recommendation, no historical callback was replayed. See the recovery and readback details in [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md).

## Phase 5 live prerequisites (2026-11-12)

The approved publisher ACP and four assignments were applied and verified; see [acp-migration-plan.md](./acp-migration-plan.md). FastAPI and the React dashboard are running and return HTTP 200. WSL can reach FastAPI at the address configured in the two existing TinyIoT subscriptions. A disposable live subscription completed TinyIoT's callback verification handshake **201 / 2001** and was cleaned up. The saved CindyPT goal returned TinyIoT GET **200 / 2000** for both container and latest CIN, and `AIRecommendations` container GET by CCindyPT returned **200 / 2000**. The dashboard CindyPT page loaded the existing stored recommendation and labels it rule-based.

The webcam model file is present; OpenCV and MediaPipe are installed in the active Python 3.14 environment. Camera 0 opened and a frame passed through the MediaPipe PoseLandmarker model successfully; that frame contained **zero detected people**. A **real short workout still requires a person in front of the camera**. No synthetic production workout summary was posted, no manual Generate Recommendation action was used, and no new workout recommendation is claimed. Therefore the real completed-summary callback, durable job transition, new stored recommendation, duplicate delivery suppression, and dashboard display of **new** advice remain **NOT VERIFIED live**.

A final read-only check retrieved the publisher ACP, both workout containers, and both guarded subscriptions at **HTTP 200 / X-M2M-RSC 2000** as CAdmin. All five RIs and four assignments matched the migration result. RepetitionCount remained at `cni=292`, `cbs=65513`; WorkoutSummary remained at `cni=4`, `cbs=529`. `GET /api/integration/metrics` still showed zero validated, duplicate, and rejected callbacks and no coaching jobs. The dashboard returned HTTP 200, but a newly generated recommendation could not be checked without a real completed workout.

---

Verified 2026-11-12. **No synthetic workout content was posted to production TinyIoT.** The complete notification-to-dashboard test below used an HTTPX mock CSE and a temporary SQLite database. It is not live TinyIoT evidence.

## Workflow traced

1. `Summary_Sub` sends a oneM2M notification to the FastAPI callback. `NotificationHub.receive` checks the callback token, oneM2M headers, origin, exact configured subscription reference, event type, and CIN identity. It retrieves the named summary CIN from TinyIoT and compares any notification representation to that stored instance.
2. Only a validated `complete` or `completed` stored summary invokes `CoachingJobs.enqueue`. The synchronous SQLite insert uses the summary resource identity as a primary key. The callback returns HTTP **200** / X-M2M-RSC **2000** without waiting for coaching work.
3. The background worker re-retrieves the stored summary and confirms its RI and completed status. It retrieves the latest saved goal and its version, applies the existing `analyze` and rule-based `recommend` functions, and derives a deterministic recommendation name from workout identity plus goal version.
4. `TinyIoTClient.create_named_recommendation` checks for a matching existing CIN, posts only if absent, then GETs the named CIN and compares stored content. A lost POST reply or repeated callback is recoverable without a duplicate recommendation. The SQLite job is marked done after this readback.
5. The worker emits a `recommendation` event. The React app listens for `summary` and `recommendation` events, refreshes the CindyPT view, and also polls every five seconds. The visible advice is labeled **RULE-BASED** and is fetched from the backend's stored recommendation endpoint. Manual recommendation generation remains available.

## Mocked integration test results

The new test in [test_coaching_integration.py](../../backend/tests/test_coaching_integration.py) submitted a completed summary callback to the FastAPI ASGI app while a mock TinyIoT CSE held the summary and current goal. The callback returned **HTTP 200 / X-M2M-RSC 2000** and created one pending SQLite job with **zero recommendation POSTs during the callback**. A new `CoachingJobs` instance reopened the same database, processed the job, read back the named stored CIN, emitted a recommendation event, and marked the job done. The stored recommendation carried `source=rule-based`, the workout identity, and goal version. `GET /api/cindypt/recommendations` returned that stored recommendation. Repeating the same callback returned HTTP 200 and left the mock CSE recommendation POST count at **one**.

Existing backend tests additionally cover malformed and interrupted summaries, unknown or tampered subscriptions, missing goals, temporary CSE failures, duplicate summary notifications, and retry after a restart. The queue persists pending jobs in SQLite with capped backoff between retries. It does not automatically change a saved training plan.

## Live evidence and blockers

- Live TinyIoT ACP reads and disposable publisher/rollback operations are documented in [acp-migration-plan.md](./acp-migration-plan.md). They did not exercise the production WorkoutSummary notification.
- FastAPI was not listening during the earlier Phase 4 audit. In Phase 5 it was started only after checking that both existing subscription URLs exactly matched configuration; no subscription URL update was needed. Server access logs were disabled because the callback token is part of the path.
- Actual TinyIoT → FastAPI notification delivery, a real completed webcam workout, a production recommendation CIN, and visible browser refresh are **NOT VERIFIED** end to end. These require the approved ACP migration and a live system run. The React build and lint checks validate code, not browser behavior.

## Regression checks

| Check | Result |
| --- | --- |
| Backend unit and mocked integration tests | 91 passed |
| Tool regression tests | 118 passed after the Phase 5 migration runner tests |
| React `npm.cmd run build` | Passed (Vite 8.3.5) |
| React `npm.cmd run lint` | Passed (oxlint) |

The local PowerShell execution policy blocks `npm.ps1`; `npm.cmd` completed both frontend checks without changing that policy.
