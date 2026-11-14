# CindySense development handoff

Date: 2026-11-15

## Completed

- Rule-based fatigue estimation works against validated historical webcam repetition data.
- The session-aware TinyIoT fatigue-history reader validates retained CINs independently and avoids merging sessions.
- The `CindyPT_AE/FatigueAnalysis` container was created and verified without changing existing ACPs or subscriptions.
- One historical test FatigueAnalysis CIN was published and read back by the approved identities.
- Deterministic naming and duplicate suppression were verified.
- The React dashboard contains a read-only fatigue panel that rejects stale, historical, and test results as live estimates.
- FastAPI now uses a bounded, session-aware fatigue cache with single-flight protection and latest-CIN invalidation.
- Automatic fatigue publication remains disabled.

## Current blocker

FastAPI was restarted successfully during the controlled recovery attempt, but its health response reports that TinyIoT is unreachable. Windows HTTP requests to TinyIoT port 3001 time out after five seconds. TinyIoT, PostgreSQL, and Windows-to-WSL connectivity still require diagnosis. The root cause is not confirmed; do not assume that the timeout is caused by TinyIoT, PostgreSQL, port forwarding, or FastAPI without further evidence.

## Tomorrow's priorities

1. Inspect TinyIoT and PostgreSQL inside WSL.
2. Diagnose the TinyIoT HTTP timeout using read-only checks and without changing stored resources.
3. Restore and verify TinyIoT connectivity safely.
4. Confirm FastAPI health and the fatigue endpoint after connectivity is restored.
5. Measure fatigue-cache performance with repeated read-only requests.
6. Start the React dashboard.
7. Perform a short webcam Cindy workout.
8. Check repetition counting, dashboard synchronization, and fatigue estimation.
9. Attempt the full 20-minute workout only after the short test passes.

Automatic fatigue publication must remain disabled.
