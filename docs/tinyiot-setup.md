# TinyIoT external dependency

TinyIoT is maintained at [seslabSJU/tinyIoT](https://github.com/seslabSJU/tinyIoT). The locally inspected WSL Ubuntu checkout was clean on `main` at commit `c140495`. Keep that checkout and its database, build files, and server configuration outside this repository. Follow its upstream README for installation and database choices; the inspected setup uses port 3000.

The backend needs `TINYIOT_BASE_URL` reachable **from the backend process**, plus local read originators configured in ignored `backend/.env`. The dashboard never calls TinyIoT directly. Start and maintain TinyIoT separately; this repository does not provision its resources or change its access policies.

The optional `TINYIOT_DISCOVERY_ORIGIN` is separate from `TINYIOT_WORKOUT_ORIGIN`. Set it only to an originator whose ACP grants discovery on the intended subtree; retrieve-only dashboard access does not imply discovery access in TinyIoT.

## Observed resource paths

The existing dashboard README recorded successful read access on 2026-10-27 for:

```text
TinyIoT/
├── CindySense_AE/
│   └── RepetitionCount/
│       └── la
└── CindyPT_AE/
    └── AIRecommendations/
        └── la
```

`la` means the latest content instance. These historical observations were **not rechecked against the live server while preparing this repository**. Confirm paths and permissions read-only in your own environment before connecting a component.

The firmware's configured target is `CindySense_AE/SensorData`. The former standalone Cindy PT script read `CindySense_AE/WorkoutSummary/la` and wrote to `CindyPT_AE/AIRecommendations`. Those paths and access rights were not verified in the original staging pass. Repository setup does not create or change containers, ACPs, subscriptions, or database records.

## Cindy PT live discovery (2026-11-07)

The inspected server at port 3001 has `CindyPT_AE` with `SemanticInsights`, `AIRecommendations`, `PerformanceAnalysis`, `NutritionGuidance`, `RecoveryRecommendations`, `WorkoutSchedule`, `TrainingPlans`, and `FitnessGoals`. `CindySense_AE/WorkoutSummary` also exists. The backend defaults to `FitnessGoals` and `AIRecommendations`; both paths remain configurable with `TINYIOT_TARGET_PLAN_RESOURCE` and `TINYIOT_RECOMMENDATIONS_RESOURCE`.

Read-only requests using the documented Cindy PT originator returned oneM2M success for `FitnessGoals`, `AIRecommendations`, and `WorkoutSummary`. Before the approved test writes, `FitnessGoals` had zero content instances; `AIRecommendations` had two; `WorkoutSummary` had one. The latest workout content was `{"rounds":1,"pullups":5,"pushups":10,"squats":15,"durationSeconds":180,"status":"completed"}`. The oneM2M instance ID supplies its identity because that payload has no workout ID. The Cindy PT FastAPI goal, progress, and recommendation GET routes also completed against this server.

An approved integration test then appended one labeled test goal to `FitnessGoals` and one rule-based recommendation to `AIRecommendations` using the documented Cindy PT originator. Both successful CIN POSTs returned HTTP 201 and oneM2M `2001`; subsequent GETs matched the submitted content. TinyIoT rejected an initial goal POST with object-valued `con` (HTTP 400, oneM2M `4000`) without storing it. The backend now sends JSON text in `con` with `cnf: application/json:0` and `Content-Type: application/json;ty=4`, matching the existing webcam writer.

Earlier local notes used an administrative originator for some reads. Replace that practice with independently authorized, least-privilege identities for the backend, Cindy PT, and firmware before sharing access with teammates. Keep all local originators and tokens out of Git and Vite environment variables.

## Read-only workflow check (2026-11-07)

On the inspected port-3001 server, `CDashboard` retrieved `CindySense_AE/RepetitionCount`, its `/la`, its content-instance listing, and a listed instance (HTTP 200, oneM2M `2000` throughout). `CCindyPT` retrieved `CindySense_AE/WorkoutSummary`, its listing and a listed instance, plus the latest `CindyPT_AE/FitnessGoals` and `CindyPT_AE/AIRecommendations` instances (HTTP 200, oneM2M `2000`). The local ignored `backend/.env` uses `CDashboard` for `TINYIOT_WORKOUT_ORIGIN` and `CCindyPT` for `TINYIOT_CINDYPT_ORIGIN`; restart FastAPI to load it. Neither identity is placed in Vite configuration. Goal and recommendation CIN writes require `CCindyPT`, as verified by the earlier approved integration test; these read-only checks made no writes.

With that configuration, FastAPI returned HTTP 200 for `/api/health`, `/api/workout/live`, `/api/workout/snapshot`, and all three Cindy PT GET routes. The shared React page displayed the labeled test goal (7 rounds in 20 minutes), the one retained completed workout (1 round, 3 minutes, 5/10/15 reps), 6 remaining rounds, and the previously saved rule-based training and recovery advice. The generate button stayed disabled because matching advice was already saved.

The webcam workout detects repetitions, sends repetition events to `CindySense_AE/RepetitionCount`, and stops its 20-minute loop when the timer finishes. Its new completion path saves a final snapshot to `CindySense_AE/WorkoutSummary` on timer completion or explicit `S` stop. Interrupted sessions are not marked completed. Cindy PT enumerates retained summaries on every progress request, ignores records without explicit `status: "complete"` or `"completed"`, and selects the newest completed record. Mocked tests confirm the new payload updates progress and can receive advice through the explicit generate route. No live summary was published during development of this completion path.

Read-only checks found `CindySense_AE/WorkoutSummary` with one retained historical test CIN. The container inherits `CindySense_ACP`: `CAdmin` has `acop: 63`, while `CCindyPT` and `CDashboard` have retrieve-only `acop: 2`. `CWebcam` received HTTP 403 / oneM2M `4103` on the AE and summary container. Thus the current webcam command's `--tinyiot-event-origin CAdmin` is also its default summary writer. The new `--tinyiot-summary-origin` option allows an independently authorized writer later. These observations infer CREATE privilege from the policy; no live summary POST has been attempted. Do not change the ACP or run a live write without explicit approval.

The plan containers `CindyPT_AE/TrainingPlans`, `CindyPT_AE/WorkoutSchedule`, and `CindyPT_AE/RecoveryRecommendations` each existed with `cni: 0` after the read-only weekly-preview and canceled-save browser checks. The parent `CindyPT_AE` references `CindyPT_ACP`, whose `pv` grants `CCindyPT` `acop: 63`; the child containers have no explicit `acpi`. This indicates inherited create and retrieve permission for the configured CindyPT originator, but no plan POST has been attempted. Saving a plan would append one named CIN to each of these three containers; approval is required before performing that live test.

## Notification integration

FastAPI can create or reuse subscriptions on the existing `CindySense_AE/RepetitionCount` and `CindySense_AE/WorkoutSummary` containers. Configure `CINDYSENSE_NOTIFICATION_TOKEN` (at least 32 random characters), `CINDYSENSE_NOTIFICATION_URL` (the full URL ending in `/api/onem2m/notify/<token>`), and `TINYIOT_SUBSCRIPTION_ORIGIN` in `backend/.env`. Start FastAPI on a Windows interface reachable from WSL, such as `uvicorn backend.main:app --host 0.0.0.0 --port 8000`. Set the URL's host to that Windows interface's IP address. Do not use `localhost`, `127.0.0.1`, or the WSL guest address in the callback URL. From WSL, check the callback with a network request to the Windows host address before relying on subscriptions; a 405 response to GET proves routing to FastAPI, while a connection failure does not. A live oneM2M verification POST is still needed to verify the full path and any firewall rules.

On startup the backend discovers existing child subscriptions and reuses a matching notification URI and `net: 3` event type. It creates a named subscription only when needed and retries setup if TinyIoT is temporarily unavailable. Verification requests receive a oneM2M `2000` acknowledgment. Change notifications must carry the configured token, oneM2M request headers, a known subscription reference, and a content instance whose identity and content match a fresh authenticated TinyIoT read. Repeated CIN deliveries are deduplicated. Browser SSE events request a fresh stored snapshot; periodic polling recovers missed notifications.

Read-only checks on the running port-3001 CSE on November 11, 2026 returned HTTP 200 for `CindySense_AE/RepetitionCount`, `CindySense_AE/WorkoutSessions`, `CindySense_AE/WorkoutSummary`, `CindyPT_AE/FitnessGoals`, and `CindyPT_AE/AIRecommendations`. They returned HTTP 404 for `CindyPT_AE/TargetPlan`, `CindyPT_AE/Recommendations`, and `Dashboard_AE/UIState`. The integration therefore keeps the existing `FitnessGoals` and `AIRecommendations` paths and does not create the missing names. `WorkoutSessions` has no runtime role in the confirmed count path.

The two source containers each have one existing `net: 3` subscription: `RepCount_Sub` and `Summary_Sub`. Both currently target `http://127.0.0.1:5000` inside WSL. Subscription details appear in the parent container's `rcn=4` response; direct GET by subscription name returned HTTP 404 on this CSE. Setup uses the embedded representation and attempts to update those named subscriptions when a reachable final callback URL is configured. No live subscription was updated during this read-only check. A temporary FastAPI process on Windows port 8001 returned HTTP 405 for GET to `/api/onem2m/notify/check` from WSL using the Windows gateway `172.31.16.1`, confirming network routing to the new POST route. Port 8000 was already occupied by another process, and the temporary process was stopped.
