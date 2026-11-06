# FastAPI contract

Run the service from the repository root as `backend.main:app`. The interactive OpenAPI page is at `http://localhost:8000/docs`. The dashboard API base defaults to `http://localhost:8000`.

## Implemented routes

| Method and path | Behavior |
| --- | --- |
| `GET /api/health` | Reports FastAPI availability separately from TinyIoT reachability and access to the configured repetition resource. |
| `GET /api/workout/live` | Reads the repetition container and its `/la` content instance; returns stored content, timestamp, and classification as a structured event or legacy unclassified value. A stored count is not a current-session exercise score. |
| `GET /api/workout/snapshot` | Lists retained repetition content instances, retrieves unseen events, and returns the latest session for each live source with absolute exercise counts and completed Cindy rounds. Events are deduplicated by `eventId`; the backend keeps a bounded in-memory cache across dashboard refreshes. |
| `GET /api/workout/webcam-status` | Reads the latest Webcam_AE/PoseData diagnostic reading for session, detected count, queue depth, retries, and overflow. It does not count repetitions. |
| `GET /api/workout/history` | Returns `available: false` and an empty `sessions` list. This route exists, but real session history is **not integrated**. |
| `GET /api/cindypt/recommendations` | Reads the latest stored Cindy PT recommendation content from TinyIoT. |
| `POST /api/dev/workout/events` | Development-only simulation content-instance write. Disabled unless explicitly enabled; also requires a loopback client, a separate write originator, and a private token of at least 32 characters. This is not a production or firmware ingestion endpoint. |

OneM2M access denial maps to HTTP 403, a missing resource to 404, an unreachable server to 503, and malformed upstream content to 502. Review the implementation in [backend/main.py](../backend/main.py) and [backend/tinyiot.py](../backend/tinyiot.py) before changing response shapes.

## Structured repetition event

The development route accepts a validated object with `eventId` (UUID), `sessionId`, `exercise` (`pullup`, `pushup`, or `squat`), nonnegative absolute `repetitions` for that exercise within the session, positive `round`, timezone-aware `timestamp`, and `source: "simulator"`. See [backend/events.py](../backend/events.py). Webcam and ESP32 writers use their existing TinyIoT paths. Completed rounds use the minimum of `pullup // 5`, `pushup // 10`, and `squat // 15`; the event's `round` field is not a completed-round count. The latest content endpoint can also report legacy numeric content without assigning it to an exercise.

The snapshot survives a dashboard refresh because the backend rereads retained instances. Its in-memory cache also preserves previously seen events after TinyIoT retention removes them. A backend restart cannot recover events already removed from TinyIoT; the response lists `observedExercises` so partial coverage is visible. The ESP32 timer starts at its first stored event because its session ID does not contain a start time. The webcam timer uses the timestamp encoded in its session ID.
The configured workout originator must be allowed to list the repetition container and retrieve its CIN children. Missing or malformed CINs cause a visible snapshot error instead of silently returning incomplete counts.
The snapshot also reports `retrievedAt`, `retrievalMs`, and retention fields. The local TinyIoT implementation was observed to ignore `lim` on `rcn=6` listings while honoring `ofst`; the backend stops when it has the container's reported `cni` instances. Configure `TINYIOT_WEBCAM_ORIGIN` for the separate pose-status read.

## Planned, not implemented

- Trusted device event ingestion and recognition-derived repetition updates.
- Real workout session storage and history retrieval.
- A stable contract connecting Cindy PT generation to completed sessions.

The dashboard uses the live webcam snapshot by default; Demo Mode remains available from its source selector.
