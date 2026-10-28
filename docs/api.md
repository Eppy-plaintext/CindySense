# FastAPI contract

Run the service from the repository root as `backend.main:app`. The interactive OpenAPI page is at `http://localhost:8000/docs`. The dashboard API base defaults to `http://localhost:8000`.

## Implemented routes

| Method and path | Behavior |
| --- | --- |
| `GET /api/health` | Reports FastAPI availability separately from TinyIoT reachability and access to the configured repetition resource. |
| `GET /api/workout/live` | Reads the repetition container and its `/la` content instance; returns stored content, timestamp, and classification as a structured event or legacy unclassified value. A stored count is not a current-session exercise score. |
| `GET /api/workout/history` | Returns `available: false` and an empty `sessions` list. This route exists, but real session history is **not integrated**. |
| `GET /api/cindypt/recommendations` | Reads the latest stored Cindy PT recommendation content from TinyIoT. |
| `POST /api/dev/workout/events` | Development-only simulation content-instance write. Disabled unless explicitly enabled; also requires a loopback client, a separate write originator, and a private token of at least 32 characters. This is not a production or firmware ingestion endpoint. |

OneM2M access denial maps to HTTP 403, a missing resource to 404, an unreachable server to 503, and malformed upstream content to 502. Review the implementation in [backend/main.py](../backend/main.py) and [backend/tinyiot.py](../backend/tinyiot.py) before changing response shapes.

## Structured repetition event

The development route accepts a validated object with `eventId` (UUID), `sessionId`, `exercise` (`pullup`, `pushup`, or `squat`), nonnegative absolute `repetitions` for that exercise and round, positive `round`, timezone-aware `timestamp`, and `source: "simulator"`. See [backend/events.py](../backend/events.py). The latest content endpoint can also report legacy numeric content without assigning it to an exercise.

## Planned, not implemented

- Trusted device event ingestion and recognition-derived repetition updates.
- Real workout session storage and history retrieval.
- A stable contract connecting Cindy PT generation to completed sessions.

Agree on these contracts before connecting the demo counters to live data. Until then, teammates can work with the existing dashboard demo values and mocked backend responses.
