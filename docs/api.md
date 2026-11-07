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
| `GET /api/cindypt/goal` | Reads the latest goal from `CindyPT_AE/FitnessGoals`; returns `available: false` for an empty container. |
| `POST /api/cindypt/goal` | Validates `targetRounds`, `durationSeconds`, and optional `label`, then appends a goal content instance to the existing FitnessGoals container. |
| `GET /api/cindypt/progress` | Compares the latest distinct completed workout summary with the stored goal. Missing goals or workouts return `available: false`. |
| `POST /api/cindypt/recommendations/generate` | Computes conservative rule-based training and recovery advice and appends it to the existing `CindyPT_AE/AIRecommendations` container. Returns 409 when a goal or completed summary is missing. |
| `POST /api/cindypt/plans/preview` | Generates a deterministic seven-day rule-based preview from the saved goal, completed summaries, and requested timezone, experience, and training days. Uses TinyIoT GETs only. |
| `POST /api/cindypt/plans` | Explicitly saves the current preview by `planId` to the existing TrainingPlans, WorkoutSchedule, and RecoveryRecommendations containers. Rejects a stale preview and verifies each named CIN. |
| `GET /api/cindypt/plans` | Retrieves the latest complete saved plan; an interrupted three-container save reports `incomplete: true` until an explicit retry completes it. |
| `GET /api/cindypt/recovery` | Retrieves the latest saved recovery guidance from RecoveryRecommendations. |
| `POST /api/dev/workout/events` | Development-only simulation content-instance write. Disabled unless explicitly enabled; also requires a loopback client, a separate write originator, and a private token of at least 32 characters. This is not a production or firmware ingestion endpoint. |

OneM2M access denial maps to HTTP 403, a missing resource to 404, an unreachable server to 503, and malformed upstream content to 502. Review the implementation in [backend/main.py](../backend/main.py) and [backend/tinyiot.py](../backend/tinyiot.py) before changing response shapes.

Cindy PT goal and recommendation writes append JSON text content instances to their two existing containers. The backend supplies `cnf: application/json:0`; its GET routes parse that JSON text back into objects. The live server accepted this format with HTTP 201 and oneM2M `2001` for a labeled test goal and a rule-based recommendation.

## Weekly plan request

The dashboard sends the browser's IANA timezone. A preview request is `POST /api/cindypt/plans/preview` with `{"timezone":"Asia/Seoul","trainingDays":[0,2,4],"experienceLevel":"beginner"}`. Weekdays are Monday `0` through Sunday `6`; one to four distinct days are accepted. The default UI selection is Monday, Wednesday, Friday and beginner experience. The backend computes the coming Monday in that timezone, using today when it is already Monday; generated dates are never before local today. The same goal, summaries, preferences, and local week produce the same `planId`.

No workout history produces a one-round starter plan and `limitedInformation: true`. With history, the latest workout and up to three recent distinct workouts inform a conservative baseline; progression is at most one round and stays below an unmet ultimate goal. Adjacent training days and extra beginner days stay lighter. Rest days carry no prescribed repetitions. The historical integration workout and test goal are identified in the dashboard; an unlabeled workout is shown as unverified rather than claimed to be real.

Saving requires a separate user-confirmed `POST /api/cindypt/plans` with `{"preferences":<same preference object>,"planId":"<preview planId>"}`. The backend recomputes the plan and rejects changed inputs with 409. It then writes three JSON-text CINs named `plan-<planId>`: the full plan to `CindyPT_AE/TrainingPlans`, its dated days to `CindyPT_AE/WorkoutSchedule`, and its recovery items to `CindyPT_AE/RecoveryRecommendations`. Each named CIN is checked before a write and read back afterward. A retry can complete a partial save without duplicating a successful earlier CIN. These containers were empty in the read-only live check; no live plan write has been performed.

## Structured repetition event

The development route accepts a validated object with `eventId` (UUID), `sessionId`, `exercise` (`pullup`, `pushup`, or `squat`), nonnegative absolute `repetitions` for that exercise within the session, positive `round`, timezone-aware `timestamp`, and `source: "simulator"`. See [backend/events.py](../backend/events.py). Webcam and ESP32 writers use their existing TinyIoT paths. Completed rounds use the minimum of `pullup // 5`, `pushup // 10`, and `squat // 15`; the event's `round` field is not a completed-round count. The latest content endpoint can also report legacy numeric content without assigning it to an exercise.

The snapshot survives a dashboard refresh because the backend rereads retained instances. Its in-memory cache also preserves previously seen events after TinyIoT retention removes them. A backend restart cannot recover events already removed from TinyIoT; the response lists `observedExercises` so partial coverage is visible. The ESP32 timer starts at its first stored event because its session ID does not contain a start time. The webcam timer uses the timestamp encoded in its session ID.
The configured workout originator must be allowed to list the repetition container and retrieve its CIN children. Missing or malformed CINs cause a visible snapshot error instead of silently returning incomplete counts.
The snapshot also reports `retrievedAt`, `retrievalMs`, and retention fields. The local TinyIoT implementation was observed to ignore `lim` on `rcn=6` listings while honoring `ofst`; the backend stops when it has the container's reported `cni` instances. Configure `TINYIOT_WEBCAM_ORIGIN` for the separate pose-status read.

## Planned, not implemented

- Trusted device event ingestion and recognition-derived repetition updates.
- Real workout session storage and history retrieval.
- A real webcam-to-TinyIoT completion run. The webcam runner now writes `WorkoutSummary` only after the 20-minute timer or an explicit `S` stop, with `sessionId`, `status: "completed"`, `completionReason`, `rounds`, `pullups`, `pushups`, `squats`, and `durationSeconds`. The inspected historical summary has the same result fields without an ID or reason. Cindy PT also accepts optional `workoutId` and an optional `reps` object (`pullup`, `pushup`, `squat`). A missing ID falls back to the oneM2M instance ID. Ongoing or statusless summaries are ignored; same-ID conflicting summaries fail visibly. Duration and reps are never inferred from live events. Mocked API coverage verifies the newly published shape updates progress and can be used by the explicit recommendation route. A real write remains untested pending approval and hardware availability.

The dashboard uses the live webcam snapshot by default; Demo Mode remains available from its source selector.
The Cindy PT page independently polls its three read-only GET routes. Its goal form writes only on submit, and recommendation generation requires confirmation; polling never creates CINs. A saved recommendation for the same workout and goal disables the generate button.
