# CindyPT automation results

Verified completed summary callbacks enqueue a SQLite job keyed by the stored summary resource ID. The worker reads the summary again, reads the latest goal and its resource version, uses the existing rule-based coach, and writes a deterministically named CIN to `AIRecommendations`. It verifies content readback. Duplicate callbacks and process restarts retain the same queue identity; failed upstream calls retry with capped exponential delay. Invalid or interrupted summaries do not produce advice. The saved training plan is never changed by this worker.

Backend mocked tests: 90 tests passed, including new success, duplicate/restart, invalid/interrupted, missing goal, temporary failure, and partial group fan-out cases. Tool tests: 110 passed. Frontend build and lint passed. A live completion-to-recommendation run remains **NOT VERIFIED**. The stored advice is labeled `rule-based`; dashboard SSE refreshes again when the worker saves it, and polling remains a fallback.

Operational limit: the queue is local SQLite. Run one FastAPI worker using a persistent writable `CINDYSENSE_COACHING_DB` path. If the file is lost, historical callbacks are not automatically replayed. Retained TinyIoT recommendations remain the source of saved advice.
