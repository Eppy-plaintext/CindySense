# CindySense oneM2M feature audit

This audit is based on the live TinyIoT instance at `127.0.0.1:3001/TinyIoT`, the CindySense repository, and the TinyIoT WSL source checkout. Live checks used authenticated GET/discovery requests only. No resource, ACP, subscription, content instance, or saved CindyPT plan was deleted or overwritten.

## Resource tree

```text
TinyIoT/
├── defaultACP
├── Dashboard_ACP
├── CindyPT_ACP
├── CindySense_ACP
├── Webcam_AE (AE-ID CWebcam)
│   └── PoseData (CNT)
├── Dashboard_AE (AE-ID CDashboard, Dashboard_ACP)
│   ├── UserPreferences (CNT)
│   └── DashboardNotifications (CNT)
├── CindyPT_AE (AE-ID CCindyPT, CindyPT_ACP)
│   ├── SemanticInsights (CNT)
│   ├── AIRecommendations (CNT) ── Recommendation_Sub (SUB)
│   ├── PerformanceAnalysis (CNT) ── Analysis_Sub (SUB)
│   ├── NutritionGuidance (CNT)
│   ├── RecoveryRecommendations (CNT)
│   ├── WorkoutSchedule (CNT)
│   ├── TrainingPlans (CNT) ── TrainingPlan_Sub (SUB)
│   └── FitnessGoals (CNT)
└── CindySense_AE (AE-ID CAdmin, CindySense_ACP)
    ├── TimeManagement (CNT)
    ├── SynchronizationStatus (CNT)
    ├── ReliabilityStatus (CNT)
    ├── NetworkMetrics (CNT) ── NetworkMetrics_Sub (SUB)
    ├── DeviceStatus (CNT)
    ├── AggregatedWorkoutData (CNT)
    ├── WorkoutSummary (CNT) ── Summary_Sub (SUB)
    ├── WorkoutSessions (CNT)
    ├── RepetitionCount (CNT) ── RepCount_Sub (SUB)
    ├── ExerciseStatus (CNT)
    └── SensorData (CNT) ── SensorData_Sub (SUB)
```

The existing `CindySense_AE/WorkoutMonitoring_GRP` groups the `RepetitionCount`,
`WorkoutSummary`, and `WorkoutSessions` CNTs. TinyIoT stores their internal
resource IDs in `mid`; the group was verified through both direct retrieval and
the read-only `/fopt` fan-out point.

The two CindySense notification subscriptions are reused by [backend/notifications.py](../backend/notifications.py). Their callback URI is configured outside source control and must be reachable from WSL. Existing subscriptions are discovered from the parent CNT representation because this TinyIoT deployment historically returned 404 for direct SUB reads until its PostgreSQL resource loader was corrected.

## Feature status

| oneM2M capability | CindySense use | Status | Evidence / demonstration |
| --- | --- | --- | --- |
| AE | Webcam, dashboard, CindyPT, and CindySense application boundaries | Implemented | Four live AEs with distinct AE-IDs |
| CNT | Sensor, pose, repetition, session, summary, goals, recommendations, and plans | Implemented | Live tree and authorized reads |
| CIN | Sensor batches, pose snapshots, repetition events, summaries, goals, advice, plans | Implemented | Idempotent names and content validation in repository |
| ACP | Least-privilege application separation | Implemented | `Dashboard_ACP`, `CindyPT_ACP`, `CindySense_ACP`, `defaultACP` live |
| SUB | Repetition and summary event delivery to FastAPI | Implemented / automatic delivery live-verified | TinyIoT now dispatches after CIN creation; FastAPI accepts TinyIoT's `/tinyiot/<SUB-RI>` references and RVI 3. Repetition and summary callbacks returned RSC 2000, emitted one SSE event each, and duplicate CIN identities were suppressed |
| Notification filtering | Filter by `enc.net`, `enc.atr`, and `enc.chty` | Live-verified with a CIN limitation | Disposable callback matrix below: `chty=[3]` distinguishes CNT/CIN; `atr=["lbl"]` distinguishes labeled/unlabeled CNT but did not notify for a labeled CIN. Production subscriptions remain `net=[3]` |
| SSE | Forward validated oneM2M events to dashboard consumers | Implemented | `/api/integration/events`; clients reconcile from TinyIoT |
| Resource discovery | Discover authorized CNT references | Implemented in backend | `/api/onem2m/discovery` uses TinyIoT `fu=1`, `rcn=11`, `ty=3` |
| GRP | Read-only fan-out of the three workout monitoring containers | Implemented / live-verified | `WorkoutMonitoring_GRP` was created once with `mt=3`, `csy=2`, and three existing CNT members; GET of the group and `/fopt` returned all three members and CNT representations |
| `mni` | Bound retained repetition history | Live-verified | Disposable CNT with `mni=2` evicted the oldest of three CINs |
| `mbs` | Byte-size retention bound | Live-verified, trigger-based | Disposable CNT with `mbs=10` retained only the newest of three 6-byte CINs; not configured on repetition/summary containers |
| `mia` | Age-based cleanup for high-volume raw data | Live-verified on new CIN create | Old CIN remained accessible until another CIN was created, then was removed; not configured on production containers |
| `et` | Resource expiration | Implemented on access | Disposable CNT remained readable before `et` and returned 404 after expiry; no separate periodic worker was observed |
| Lifecycle metadata | `ct`, `lt`, `st`, `cni`, `cbs`, and retention attributes | Implemented | Returned by CNT/CIN representations and used for reconciliation |
| Aggregation / fan-out | Group retrieval of monitoring CNT metadata | Implemented / live-verified | Read-only GET `/WorkoutMonitoring_GRP/fopt` returned one response per member; no mutating fan-out was used |
| Specialized resources | FCNT (28), TS (29), TSI (30) | Basic CRUD live-verified on disposable resources (2026-11-11); not in production | FCNT needs a registered SDT schema; TS analytics behavior beyond one TSI/counter was not tested |

## Source-supported operations

TinyIoT defines CREATE, RETRIEVE, UPDATE, DELETE, NOTIFY, and DISCOVERY. HTTP GET with `fu=1` selects discovery; discovery accepts `rcn=11` or child references. The server enforces ACP checks for discovery candidates, so the new backend endpoint does not expose resources an originator cannot discover. Configure `TINYIOT_DISCOVERY_ORIGIN` with a narrowly authorized discovery identity; it is intentionally separate from the dashboard read origin.

Subscription matching checks `enc.net`, and the implementation also evaluates `enc.atr` and `enc.chty`. The CindySense subscriptions use `net=3` (create of a direct child) and `nct=1` so a new CIN representation is delivered. The backend verifies the subscription URI, notification token, originator headers, subscription reference, CIN identity, and stored CIN content before publishing an SSE event.

## HTTP 403 correction

The previous 403/RSC 4103 came from probing the CSE root as `CDashboard`. `defaultACP` does not grant that originator retrieve access at the root, while the dashboard is authorized for its application resources and retrieve-only access to CindySense resources. [backend/main.py](../backend/main.py) now health-checks the already-authorized `CindySense_AE/RepetitionCount` container directly, eliminating the unauthorized root probe without broadening ACPs.

## Automatic notification failure and fix

TinyIoT's `main.c` freed a newly created CIN runtime node before the common
`notify_via_sub()` call, so the create returned HTTP 201 but no notification was
dispatched. The WSL TinyIoT source was rebuilt with notification dispatch moved
before that free. The sender also emits oneM2M RVI 3, which is valid for this
TinyIoT build; FastAPI now accepts RVI 3 or 4. TinyIoT formats `sur` as
`/tinyiot/<subscription-ri>`, so the backend matches the exact subscription RI
as well as structured subscription paths.

Live results after the fix:

- Repetition CIN: HTTP 201 / RSC 2001; automatic callback: HTTP 200 / RSC 2000; SSE: one `repetition` event.
- WorkoutSummary CIN: HTTP 201 / RSC 2001; automatic callback: HTTP 200 / RSC 2000; SSE: one `summary` event.
- Repeated summary delivery: HTTP 200 / RSC 2000; no second SSE event.

## Recommended demonstration order

1. Run `/api/onem2m/discovery` and show that ACP-scoped discovery returns the CindySense CNT references.
2. Start the webcam and show a repetition CIN arriving under `RepetitionCount`; a validated callback returns RSC 2000 and the dashboard SSE stream emits one repetition event.
3. Complete or stop a Cindy workout and show the summary CIN, `Summary_Sub` callback, and CindyPT refresh. A duplicate delivery is acknowledged but does not refresh the event twice.
4. Retrieve `WorkoutMonitoring_GRP/fopt` as the authorized monitoring origin to demonstrate oneM2M fan-out over the existing CNTs.
5. Show the CNT's `mni`/`cni` metadata and explain bounded retention.
6. Show the ACP matrix and explain why CSE-root access is intentionally denied.

## Deferred changes

Do not change `mbs`/`mia`, add `et` to existing resources, or introduce FCNT/TS resources during the hackathon demo without a separate compatibility test. Those changes can evict or expire existing data, change notification volume, or add unsupported behavior to the current dashboard contract. The monitoring GRP is safe to retain because it references existing CNTs and was verified with read-only retrieval and fan-out.

## Remaining meaningful candidates before the hackathon

1. Add a read-only dashboard panel for discovery results and GRP fan-out metadata.
2. Add a reconciliation endpoint that lists retained CIN references after an SSE reconnect.
3. Use `atr` or `chty` on new subscriptions only with a workload-specific live check; `atr` did not match a labeled CIN in the disposable test below.
4. Add a bounded `mia` policy to raw `PoseData` only after exporting and verifying a complete 20-minute retention run.
5. Add a live ACP matrix check for `CDashboard`, `CCindyPT`, and `CWebcam` as a preflight diagnostic.

FCNT/TS resources, mutating GRP fan-out, new AEs, and expiration policies on workout summaries are not recommended before the demo because they add compatibility or data-retention risk without improving the core Cindy flow.

## Live advanced-filter and lifecycle verification (2026-11-11)

The CSE was checked through its WSL listener at `172.31.28.214:3001` after the Windows localhost forwarding path stopped responding. The production tree was read-only during the final verification.

### Notification filters

The local TinyIoT source implements all three requested filter dimensions:

- `enc.net` is required before dispatch; a subscription that does not include the event's notification event code is skipped.
- `enc.atr` is evaluated by checking that every named attribute exists in the request resource representation.
- `enc.chty` is evaluated against the created/updated resource type.

`RepCount_Sub`, `Summary_Sub`, and `SensorData_Sub` were read back after the initial experiments and each still had `enc.net=[3]`. No production subscription was updated. The initial attempt to create a disposable CNT at the CSE root closed TinyIoT's listener; the service was restarted from the unchanged local binary. Later probes created their test CNT only under `CindySense_AE`.

### Live filter matrix (2026-11-11)

The isolated [filter probe](../tools/onem2m_filter_probe.py) created one uniquely named CNT under `CindySense_AE` with two child SUBs: `enc={"net":[3],"atr":["lbl"]}` and `enc={"net":[3],"chty":[3]}`. A temporary HTTP callback collector acknowledged notifications with HTTP 200 / oneM2M RSC 2000. Verification requests were excluded from event counts. The same matrix was observed twice; the final run recorded:

| Direct-child create | Create HTTP / RSC | `atr=["lbl"]` notifications | `chty=[3]` notifications |
| --- | --- | ---: | ---: |
| Unlabeled CIN (`ty=4`) | 201 / 2001 | 0 | 0 |
| Labeled CIN (`ty=4`) | 201 / 2001 | 0 | 0 |
| Unlabeled CNT (`ty=3`) | 201 / 2001 | 0 | 1 |
| Labeled CNT (`ty=3`) | 201 / 2001 | 1 | 1 |

Thus `enc.chty` is genuinely working for the tested CNT-versus-CIN distinction, and `enc.atr` is genuinely working for presence of `lbl` on a new CNT. `enc.atr` is **not reliable for labeled CIN create notifications** in this build: the labeled CIN was stored and returned by TinyIoT but produced zero callbacks for the attribute-filtered SUB. Source inspection explains the likely cause: `isAptEnc` looks up attributes in the request representation using `target_rtnode->ty`; on CIN creation the notification path passes the parent CNT node, so it looks for `m2m:cnt` instead of the request's `m2m:cin`. Do not apply this filter to repetition-event or summary CIN subscriptions without fixing and retesting TinyIoT.

The probe compared `RepCount_Sub` and `Summary_Sub` resource IDs, `enc`, and callback URIs before and after: unchanged. Only the temporary subtree was deleted (HTTP 200 / RSC 2002); readback returned HTTP 404 / RSC 4004. The live probe asserts the measured matrix, and [probe regression tests](../tools/tests/test_onem2m_filter_probe.py) protect callback counting and the expected result contract.

### Resource lifecycle

Disposable containers were created under a uniquely named child of `CindySense_AE` and removed afterward. The observed results were:

- An earlier shell probe incorrectly reported create-time `mni`/`mbs`/`mia` values as unset because its response parser selected the wrong object key. The later direct JSON probe below supersedes that result.
- `et`: a disposable CNT was readable before its expiration timestamp and returned 404 after the timestamp elapsed. This is genuine access-time expiration enforcement; a background expiration worker was not observed.

The live production metadata remains unchanged: `RepetitionCount` is `mni=1000`, `WorkoutSummary` is `mni=100`, and `SensorData` is `mni=100` with `mbs=524288`; all queried `mia` values are unset. No destructive retention change was applied.

### Safe recommendations

Keep the existing production values for the demo. If a separately approved retention migration is needed later, use `mni=1000` for `RepetitionCount`, `mni=100` for `WorkoutSummary`, retain `SensorData`'s current `mni=100`/`mbs=524288`, and introduce a small `mia` only on disposable or exported raw pose data after a retention rehearsal. Do not add `et` to workout history containers. The existing notification unit tests already regression-test `net=3` subscription creation, named subscription reuse, and duplicate delivery suppression; no production test fixtures or webcam/detection logic were changed in this pass.

## Retention and ACP live verification (2026-11-11)

The [retention/ACP probe](../tools/onem2m_retention_acp_probe.py) created a unique subtree directly under `CindySense_AE`, never under the CSE root. It used separate child CNTs for each retention policy. The source paths are `update_cnt_cin` in TinyIoT `onem2m.c` (age cleanup on CIN create), `delete_cin_under_cnt_mni_mbs` in `util.c` (count/byte eviction), and `isETvalid`/`isExpired` in `util.c` (creation validation and access-time expiration). The probe stopped if an attribute update was not reflected. Its temporary subtree was deleted with HTTP 200 / RSC 2002 and then returned HTTP 404 / RSC 4004.

### Exact retention results

All policy CNT creates returned HTTP 201 / RSC 2001; each readback returned HTTP 200 / RSC 2000 with its requested create-time value (`mni=2`, `mbs=10`, `mia=2`). Each PUT of the same value returned HTTP 200 / RSC 2004, and readback still showed that value. All CIN creates returned HTTP 201 / RSC 2001.

| Policy | Observed readback after test | Result |
| --- | --- | --- |
| `mni=2` | First CIN 404 / 4004; second and third 200 / 2000; CNT `cni=2` | Oldest evicted after third create |
| `mbs=10` | Three 6-byte CINs: first and second 404 / 4004, third 200 / 2000; CNT `cni=1`, `cbs=6` | Byte ceiling enforced by oldest-first eviction |
| `mia=2` seconds | Old CIN still 200 / 2000 after 3.2 seconds; after creating new CIN, old 404 / 4004 and new 200 / 2000; `cni=1` | Age cleanup occurs on subsequent CIN create, not merely after wall-clock time |
| `et` roughly 4 seconds ahead | Disposable CNT 200 / 2000 before expiry, 404 / 4004 after 5 seconds | Access-time expiry enforced; no periodic deletion demonstrated |

No production retention attribute or workout data was changed. A past-expiration create rejection and expiry after server restart were not tested. `mia` scans at most 128 expired CINs per insertion in this source, so a large backlog needs separate validation.

### ACP definitions and observed access

TinyIoT `acop` bits include CREATE=1, RETRIEVE=2, UPDATE=4, DELETE=8; `63` grants all supported operations. `defaultACP` grants `all` CREATE (`1`) on the CSE root and `CAdmin` full self-privileges; do **not** treat it as permission for arbitrary clients to read or mutate existing resources. `CindySense_ACP` grants `CAdmin` `63` and `CDashboard`/`CCindyPT` retrieve (`2`). `CindyPT_ACP` grants `CCindyPT` `63` and `CAdmin`/`CDashboard` retrieve (`2`). `Dashboard_ACP` grants `CDashboard` `63` and `CAdmin` retrieve (`2`). The three application ACPs' self-privileges (`pvs`) grant their respective owners `63`.

The matrix below uses `R` for live GET HTTP 200 / RSC 2000 and `-` for HTTP 403 / RSC 4103. `W` describes intended CREATE/UPDATE/DELETE permissions inferred from the owning AE's ACP; **writes to production target containers were not attempted**. A separate disposable write matrix follows.

| Target | `CAdmin` | `CDashboard` | `CCindyPT` | `CWebcam` |
| --- | --- | --- | --- | --- |
| `CindySense_AE/RepetitionCount` | R + admin W | R | R | - |
| `CindySense_AE/WorkoutSummary` | R + admin W | R | R | - |
| `Webcam_AE/PoseData` | R + admin W | - | - | R + owner W |
| `CindyPT_AE/FitnessGoals` | R + admin W | R | R + owner W | - |
| `CindyPT_AE/AIRecommendations` | R + admin W | R | R + owner W | - |
| `Dashboard_AE` | R + admin W | R + owner W | - | - |

Live disposable writes were tested separately under uniquely named CNTs in each AE. On a test CNT under `CindySense_AE`, `CAdmin` CREATE/UPDATE/DELETE returned respectively 201/2001, 200/2004, 200/2002; each of the other three origins returned 403/4103 for all three operations. The [application-AE write probe](../tools/onem2m_acp_write_probe.py) repeated these operations under temporary `Webcam_AE`, `CindyPT_AE`, and `Dashboard_AE` CNTs: `CAdmin` and each AE's own identity succeeded with the same statuses, while the two unrelated application identities received 403/4103. Every test subtree was deleted (200/2002) and then returned 404/4004. These are tests of inheritance and sibling write behavior, **not proof of write rights on each named production CNT**.

`CAdmin` could write in `CindyPT_AE` and `Dashboard_AE` despite their ACP entries listing it as retrieve-only. The effective administrative bypass is broader than those `acop` entries suggest. The [webcam client](../tools/webcam_tinyiot.py) defaults its low-level origin to `CAdmin`, although its CLI defaults to `CWebcam`; its `setup` uses `CAdmin` for existing-AE/CSE inspection, and `ensure_event_retention` uses `CAdmin` to update `RepetitionCount` if needed. Since `CWebcam` was denied GET on `RepetitionCount` and `WorkoutSummary`, simply replacing `CAdmin` for repetition/summary publishing would break that workflow. The backend uses configured read identities and a separate subscription origin; if that subscription origin is `CAdmin`, it has the same broad authority. A future least-privilege change should grant a narrowly scoped publisher CREATE/RETRIEVE on the two event CNTs and test the full publish/readback/notification flow before changing origins or ACPs. Keep administrative retention changes out of normal publishing paths. No ACP was modified here.

The [retention regression test](../tools/tests/test_onem2m_retention_acp_probe.py) checks the observed eviction/expiration contract; the [ACP regression test](../tools/tests/test_onem2m_acp_write_probe.py) detects unintended cross-application writes. Final read-only checks returned HTTP 200 / RSC 2000 for `RepetitionCount`, `WorkoutSummary`, `WorkoutMonitoring_GRP`, and its `/fopt`; FastAPI `/api/workout/snapshot` returned HTTP 200. Direct SUB readback showed `RepCount_Sub` and `Summary_Sub` still had `enc.net=[3]` and their original resource IDs. The focused notification, snapshot, retention, and ACP tests passed (25 tests total). Before the demo, confirm the running webcam command's actual origin and rehearse those health checks. Do not add production `mia` or `et` without a separate export-and-restore plan.

## End-to-end integration and reliability audit (2026-11-11)

### Identity and permission boundary

The runtime webcam command in [webcam_squat.py](../tools/webcam_squat.py) defaults pose publishing to `CWebcam` but repetition events to `CAdmin`; summary publishing uses the event origin unless separately overridden. The low-level [webcam client](../tools/webcam_tinyiot.py) also defaults to `CAdmin` when constructed without an explicit origin. `CWebcam` has owner rights on `Webcam_AE/PoseData` but was denied GET on both `CindySense_AE/RepetitionCount` and `CindySense_AE/WorkoutSummary` (403/4103 in the earlier live ACP audit). The repetition publisher needs GET for named-CIN idempotency and POST to create; the summary publisher additionally GETs its parent CNT. Changing the runtime origin to `CWebcam` now would break the confirmed publishing path. No originator or ACP was changed.

Recommended future identity: `CWorkoutPublisher`, with CREATE on the two target CNTs and RETRIEVE on those CNTs and their own named CINs only. Do not grant UPDATE, DELETE, or broad AE/CSE privileges. First test TinyIoT's actual ACP inheritance and subscription-delivery behavior on disposable resources; then grant the exact production permissions in a reviewed migration, verify readback plus both callback flows, and only then switch the two webcam runtime flags. Remove or disable privileged retention-update code from the normal publisher launch path. Do not infer that a retrieve-only ACP entry limits `CAdmin`: this build's administrative bypass was observed directly.

### Verification scope and results

The [simulated round integration test](../backend/tests/test_round_integration.py) uses the real webcam event/summary publisher methods, FastAPI callback route, notification validation, SSE response generator, workout snapshot, and CindyPT progress/preview routes against an in-memory TinyIoT transport. It publishes one full Cindy round: 5 pull-ups, 10 push-ups, and 15 squats. All 30 CIN creates returned oneM2M RSC 2001; all 30 corresponding callbacks returned HTTP 200 / RSC 2000 and emitted a repetition SSE signal. The snapshot reported counts 5/10/15, one completed round, and 30 structured events. A completed summary created one CIN, returned callback HTTP 200 / RSC 2000, emitted the summary SSE signal, and produced available CindyPT progress plus a current rule-based recommendation for that session. Re-publishing an event and summary verified the existing CINs without duplicates; a duplicate callback did not produce another SSE signal. A simulated failed POST (503) was retried, then appeared in the snapshot; an altered callback representation was rejected with HTTP 400. This is **not** a physical webcam or ESP32 test, and the mock CSE is not a substitute for a live TinyIoT delivery test.

The dashboard wiring is present: [App.jsx](../dashboard/src/App.jsx) increments the repetition revision on SSE `repetition`, triggering a snapshot refetch; it increments the summary revision on `summary`, passed to [CindyPtPage.jsx](../dashboard/src/components/CindyPtPage.jsx) to refetch progress, stored recommendation, and current preview. The SSE `reconcile` event also refetches after connection. This was inspected and supported by backend SSE tests, but the browser rendering itself was **not** observed during this pass. Importantly, a summary callback automatically refreshes the **preview** and progress; it does not automatically create or overwrite a saved `AIRecommendations` CIN or saved CindyPT plan. Saving a recommendation still requires the explicit generate action.

Read-only live checks returned HTTP 200 for FastAPI `/api/workout/snapshot` (available from TinyIoT), `/api/cindypt/progress` (available), and `/api/cindypt/recommendations/current` (available, rule-based preview). Direct TinyIoT GET returned HTTP 200 / RSC 2000 for both event CNTs, `WorkoutMonitoring_GRP`, `RepCount_Sub`, and `Summary_Sub`; both subscriptions still had `enc.net=[3]`. No production write, subscription update, or live event injection was done in this pass. Prior live automatic-delivery verification remains historical evidence; **new** physical camera-to-CSE-to-SSE delivery and ESP32 synchronization are unverified here.

### Safe 20-minute reliability rehearsal

The earlier monitor accepted only SSE event names and inferred an approximate latency from the most recent snapshot. That limitation is superseded by the event-level observer described below. The full 20-minute rehearsal has **not** been run.

The earlier SSE payload was `{}` and prevented event-level correlation. The new contract below supersedes that limitation; keep the historical test results above separate from results obtained after restarting FastAPI with the new code.

Known reliability limits: the backend's notification deduplication is process-local (up to 4096 IDs), the SSE listener queue is bounded at 16 and drops signals when full, but dashboard reconnect/polling reconciles retained state. Repetition events queued only in webcam RAM can be lost on abrupt process death before the shutdown spool is written; normal shutdown spools and replays. Snapshot correctness depends on retained CINs and cached events; retention can remove history before recovery. The saved recommendation is not auto-generated. Do not present the simulation as physical webcam/ESP32 evidence.

Focused verification: 26 notification/snapshot/round/monitor Python tests passed (including out-of-order callback reconciliation); 24 CindyPT/API Python tests passed; 5 dashboard live-workout state tests passed with Node's `--test-isolation=none`. The default Node test isolation could not spawn a child process in this Windows sandbox (`EPERM`), so the same test file was rerun without isolation. The broader webcam spool suite encountered Windows `Access is denied` while creating its pre-existing `data/recordings` temporary directory and was not counted as passing. The integration and monitor files had no editor-reported errors; `git diff --check` passed. No physical webcam/ESP32 test or full 20-minute load run was performed in this pass.

## Event-level reliability instrumentation (2026-11-11, prepared for physical test)

[NotificationHub](../backend/notifications.py) now takes the repetition event ID, sequence, session, source, and event timestamp from a **validated TinyIoT CIN readback**, not from an untrusted notification representation. SSE `repetition` retains its event name and adds those fields plus `cinCreatedAt` (when the CSE supplies it), `callbackReceivedAt`, and `sseEmittedAt`. `summary` and `reconcile` event names remain unchanged, and the dashboard can continue ignoring `data`. The FastAPI [integration metrics route](../backend/main.py) exposes aggregate validated, duplicate, and rejected callback counts and SSE queue drops; it exposes no notification token or CIN content. Counters are process-local and reset on restart. Rejected callbacks include invalid requests from *any* sender and therefore do not, by themselves, identify a failed workout callback.

The [reliability monitor](../tools/monitor_cindy_reliability.py) uses only GETs: it baselines existing RepetitionCount CIN references, then reads new CINs as `CDashboard`, listens to SSE, and polls the snapshot and aggregate callback metrics. It correlates new stored webcam event IDs with SSE IDs; counts duplicate SSE IDs, stored IDs without an observed SSE signal, internal gaps in stored event sequences, and sequence inversions in SSE arrival. It reports event-to-callback, callback-to-SSE, SSE-to-observer, and event-to-observer latency distributions only when timezone-aware timestamps yield nonnegative, bounded values. An optional append-only webcam console log comparison distinguishes detections logged by the webcam from publisher-confirmed writes and stored CINs. This does **not** measure missed exercise detections: that requires physical ground truth or a separately recorded video review.

Start the current FastAPI code before the workout, ensure the existing subscription is healthy, and run this from the repository root while collecting the webcam console output to a pre-existing log file if desired:

```powershell
& 'C:/Users/USER/AppData/Local/Programs/Python/Python314/python.exe' tools/monitor_cindy_reliability.py --duration-seconds 1200 --cse-base http://127.0.0.1:3001/TinyIoT --cse-origin CDashboard --publisher-log data/recordings/webcam-workout.log
```

Omit `--publisher-log` when no append-only webcam log is available. Start the monitor **before** the workout; no production write or extra SUB is required. The final scan runs after a two-second settling window. Inspect `csePollFailures`, `metricsPollFailures`, and `reconnects` before interpreting any missing ID as a delivery gap. A stored CIN with no SSE observation can be caused by a callback failure, backend deduplication after a reconnect, SSE queue overflow, or monitor outage; it is **not** automatically a webcam detection error. The `callbackFailures` figure is the delta in rejected callback requests at FastAPI, and `sseQueueDrops` is separate. Producer-to-CSE loss is measurable only when the optional webcam log contains `[REP]` and confirmation lines for the same interval. TinyIoT retention may remove CINs before they can be scanned. The short smoke run after these changes recorded two successful snapshot polls, four successful CSE scans, one SSE reconcile, no new workout event, and two metrics poll failures because the already-running FastAPI process had not loaded the new route. It provides **no physical loss or latency measurement**.

Automated tests now cover full-round SSE fields, duplicate suppression, malformed callback rejection, out-of-order callbacks, reconnect/reconciliation of a stored event, read-only CSE correlation, invalid timestamps, metric resets, missing/duplicate/out-of-order IDs, and latency math. These tests are simulated/in-memory; physical webcam and ESP32 reliability remain untested. No production resource, ACP, subscription, workout history, detection threshold, UI layout, audio behavior, or CindyPT plan was changed.

Final focused run: 56 Python tests passed (round integration, notifications, workout snapshot, monitor, CindyPT and API); 5 dashboard live-workout state tests passed using `node --test --test-isolation=none`. The editor reported no errors in the changed Python files. The running FastAPI process still needs a normal restart to serve the new SSE payload and metrics route before a physical test; it was not restarted during this read-only smoke check.

## Advanced discovery, FlexContainer, and TimeSeries (2026-11-11)

The installed TinyIoT WSL checkout was inspected **read-only** at `/home/eppy-04/tinyIoT/source/server`. `filterCriteria.c` declares `lbl`, `ty`, `cra`, `crb`, `ms`, and `us`; `postgresql_implement.c` applies label/type and creation/last-modified timestamp predicates to scoped discovery SQL. `onem2m.c` dispatches FCNT (type 28), TS (29), and TSI (30) creation and update; `resources/fcnt.c`, `resources/ts.c`, and `resources/tsi.c` implement their handlers. FCNT creation calls `sdt_validate_fcnt`, which requires a registered container definition and rejects arbitrary custom attributes. The `genericInterworkingService` specialization is registered in `sdt_definitions/genericInterworking.fcp`. Source inspection alone does not prove which commit is running; the live results below establish the exercised behavior.

The repeatable [advanced probe](../tools/onem2m_advanced_probe.py) used `CAdmin` and created only `CindySense_AE/AdvancedProbe_4595d7ee6092` under the existing AE in the final run. It issued a health GET after every mutation, stopped on unexpected results, deleted only this unique subtree, and confirmed it absent. No production workout resource, history, subscription, ACP, CindyPT record, firmware, or webcam logic changed. `CAdmin` success does **not** establish least-privilege access for another originator. The earlier CSE-root crash path was never used.

| Live operation or case | HTTP | X-M2M-RSC | Observed result |
| --- | ---: | ---: | --- |
| Probe root CNT POST / GET | 201 / 200 | 2001 / 2000 | Isolated AE child created and readable |
| Labeled CNT, plain CNT, CIN POST | 201 each | 2001 each | Three distinct discovery targets created |
| Label match / label miss (`lbl`) | 200 each | 2000 each | Only `labeled` / no references |
| Type CNT (`ty=3`) / type CIN (`ty=4`) | 200 each | 2000 each | `labeled, plain` / `sample` |
| `cra` older cutoff / `crb` older cutoff | 200 each | 2000 each | All three / none |
| `cra` future cutoff / `crb` future cutoff | 200 each | 2000 each | None / all three |
| Labeled CNT PUT | 200 | 2004 | Label update accepted |
| `ms` older / `ms` future / `us` older / `us` future | 200 each | 2000 each | All three / none / none / all three |
| FCNT POST / GET / PUT / DELETE | 201 / 200 / 200 / 200 | 2001 / 2000 / 2004 / 2002 | Registered generic specialization created; `gisn` read back; label update accepted; deleted |
| FCNT type discovery (`ty=28`) | 200 | 2000 | Only `schema_probe` |
| FCNT absent GET | 404 | 4004 | Deletion confirmed |
| TS POST / GET / PUT / DELETE | 201 / 200 / 200 / 200 | 2001 / 2000 / 2004 / 2002 | Basic time series lifecycle works |
| TS type discovery (`ty=29`) | 200 | 2000 | Only `series_probe` |
| TSI POST / GET / DELETE | 201 / 200 / 200 | 2001 / 2000 / 2002 | One point stored/read/deleted; TS `cni` changed 0→1→0 |
| TS absent GET | 404 | 4004 | Deletion confirmed |
| Probe root DELETE / absent GET | 200 / 404 | 2002 / 4004 | Only disposable subtree removed |

Discovery requests used `fu=1&rcn=11`, scoped to the probe root. Timestamp checks used deliberately old (`20000101T000000`) and future (`20990101T000000`) cutoffs, so they verify inclusion/exclusion but **not** subsecond boundary semantics, timezone handling, or pagination. `ms`/`us` were tested after a label PUT, but no precise between-create-and-update cutoff was asserted. Combined filters, unauthorized discovery, and large result sets remain unverified. The test did not attempt TSI PUT because TinyIoT's `onem2m.c` explicitly rejects immutable TSI updates; FCNT and TS PUT were verified.

Recommendation for the webcam-only hackathon: scoped label/type/timestamp discovery is demonstrably usable for optional **read-only** diagnostics, with server-side result validation. Do not replace the working workout queries immediately. FCNT is only **partially suitable** for structured workout sessions: basic operations work, but a workout-specific SDT schema is not registered; using an unrelated generic specialization would misrepresent the data. Defer production FCNT until a reviewed schema and migration exist. TS/TSI basic storage works, but rollups, missing-data monitoring, high-rate retention, concurrency, authorization under a narrow identity, and integration with current workout summaries have **not** been demonstrated. Keep TS experimental rather than adding it to the production workflow before the hackathon. Run the offline [probe regression tests](../tools/tests/test_onem2m_advanced_probe.py) for request format, discovery parsing, and health-stop behavior; re-run the live probe only deliberately against a healthy disposable environment.

Final read-only health check: `RepetitionCount`, `WorkoutSummary`, and `WorkoutMonitoring_GRP` each returned HTTP 200 / RSC 2000; the final probe root returned HTTP 404 / RSC 4004. Seven focused offline tests passed (advanced discovery probe, notification-filter probe, retention probe); `git diff --check` passed. This did not test a physical webcam workout or ESP32 path.

## Workout-backed FCNT and TS/TSI follow-up (2026-11-11; stopped at preflight)

Target: workout ID `final-summary-097c0172`, with claimed webcam counts 5 pull-ups, 10 push-ups, 15 squats, 1 completed round, and 1200 seconds. **These values were supplied for this test but could not be verified against the stored summary.** A direct GET of `CindySense_AE/WorkoutSummary/final-summary-097c0172` returned HTTP 404 / RSC 4004; that workout ID may be inside a CIN rather than its resource name. Read-only CIN discovery on `WorkoutSummary` returned HTTP 200 / RSC 2000 and four CIN references. Individual CIN GETs then timed out after 5 seconds without an HTTP response; a final GET of `CindySense_AE` also timed out after 3 seconds. The listener remained present on WSL port 3001, but application responses were not healthy. The FastAPI workout snapshot returned HTTP 200 **before** the timeout sequence; CindyPT progress returned HTTP 503 during it. This does not prove a crash or identify its cause, but it fails the required stable-CSE preflight. No further oneM2M requests or resource writes were made after that final failed health check.

| Follow-up check | HTTP | X-M2M-RSC | Result |
| --- | ---: | ---: | --- |
| Named workout-summary CIN GET | 404 | 4004 | ID is not a directly addressable CIN name |
| Summary CIN discovery (`fu=1&rcn=11&ty=4`) | 200 | 2000 | Four references; target content not confirmed |
| Individual summary CIN GET | No response (5-second timeouts) | Not received | Aborted preflight; no content-integrity result |
| Final AE health GET | No response (3-second timeout) | Not received | CSE not responsive enough for safe writes |
| FastAPI `/api/workout/snapshot` | 200 | Not applicable | Returned before CSE became unresponsive; body not checked |
| FastAPI `/api/cindypt/progress` | 503 | Not applicable | Progress unavailable at the time of the check |
| FCNT/TS/TSI POST, GET, PUT, DELETE with this workout | Not attempted | Not applicable | Blocked by preflight; no disposable subtree created |
| Production SUB, GRP, `/fopt` follow-up | Not attempted | Not applicable | Previously verified; current operation cannot be claimed while CSE is unresponsive |

The installed TinyIoT source `resources/fcnt.c` calls `sdt_validate_fcnt`; `sdt.c` loads `.fcp` specialization files through `sdt_init`, called at server initialization in `main.c`. Read-only search of the installed `sdt_definitions` found no CindySense schema. Registering a workout FCNT would require adding a new CSE-side schema and restarting TinyIoT, changing production server behavior. No schema was registered, and the unrelated generic-interworking specialization from the earlier lifecycle probe was **not** used to store workout data. FCNT workout integration is therefore blocked under the current constraints. The source still implements TS/TSI creation and TS update; `onem2m.c` rejects TSI update as immutable. Earlier disposable generic TS/TSI lifecycle results above remain valid historical evidence, but do **not** establish workout-backed data integrity.

The new offline [workout TS integrity helpers](../tools/workout_ts_integrity.py) fail closed on mismatched workout ID, source, counts, rounds, duration, or CSE timestamp, and can build and verify one JSON-valued TSI per confirmed workout using the source CIN creation time as `dgt`. Its [three synthetic tests](../tools/tests/test_workout_ts_integrity.py) passed, together with four existing advanced-probe tests. Synthetic values are **not** evidence that the target workout is stored correctly, and no TS payload was sent. Before any later live attempt, restore CSE responsiveness through normal operator procedures, verify the target CIN content by read-only discovery/readback, confirm both FastAPI endpoints and existing SUB/GRP reads, then run only a uniquely named disposable TS probe. Do not change production resources or register an FCNT schema as part of this blocked test.

## Workout-backed TimeSeries live verification (2026-11-11; subsequent healthy run)

The earlier failed preflight above remains historical evidence. On this later run, exactly one WSL `server` process (PID 41294) was listening on port 3000; Windows port 3001 returned an AE GET with HTTP 200 / RSC 2000. FastAPI `/api/workout/snapshot` and `/api/cindypt/progress` both returned HTTP 200 before the experiment. No second TinyIoT process was started.

The latest WorkoutSummary CIN (`/la`) returned HTTP 200 / RSC 2000. Its stored `con` had `sessionId=final-summary-097c0172`, `source=webcam`, `status=completed`, `rounds=1`, `durationSeconds=1200`, and `pullups=5`, `pushups=10`, `squats=15`. Its CIN resource ID was `4-20261111T042142,4950007`, and its CSE creation time was `20261111T042142,495`. The [workout TS probe](../tools/onem2m_workout_ts_probe.py) validated those stored fields before its first write. It then created only `CindySense_AE/WorkoutTSProbe_80704ff0f080` (CNT) with child `WorkoutSeries` (TS) and one `workout_measurement` (TSI). The TSI content preserved the workout ID, all three counts, rounds, duration, source, and original CIN creation time; `dgt` was set to that same CSE creation time. This is a **storage timestamp**, not a measured physical exercise time. There was only one trustworthy timestamp in the source CIN, so the test deliberately used one TSI rather than inventing separate timestamps for each exercise.

| Live operation | HTTP | X-M2M-RSC | Verified result |
| --- | ---: | ---: | --- |
| Disposable root CNT POST / GET | 201 / 200 | 2001 / 2000 | UUID-named child under existing AE |
| TS POST / initial GET | 201 / 200 | 2001 / 2000 | `ty=29`, `mni=3`, `mbs=4096`, `cni=0` |
| TSI POST / GET | 201 / 200 | 2001 / 2000 | Readback matched all validated source-workout values and timestamp |
| TS GET with TSI | 200 | 2000 | `cni=1`, `cbs=176` |
| TS PUT / updated GET | 200 / 200 | 2004 / 2000 | Disposable label stored; `cni` remained 1 |
| TSI DELETE / TS GET | 200 / 200 | 2002 / 2000 | `cni=0`, `cbs=0` |
| TS DELETE / absent GET | 200 / 404 | 2002 / 4004 | TS deleted |
| Disposable root DELETE / absent GET | 200 / 404 | 2002 / 4004 | Only test subtree removed; no leftover |

Read-only before/after GETs of production `WorkoutSummary`, `RepetitionCount`, `WorkoutMonitoring_GRP`, `RepCount_Sub`, and `Summary_Sub` all returned HTTP 200 / RSC 2000, and their full representations had matching fingerprints. The group `/fopt` returned HTTP 200 / RSC 2000 both times. FastAPI snapshot and CindyPT progress returned HTTP 200 again afterward. This confirms the subscriptions stayed configured and readable; **automatic notification delivery was not retested**, since this TS experiment did not create a production repetition or summary event. No production retention, ACP, subscription, history, recommendation, plan, webcam, dashboard, audio, or ESP32 data was changed.

The repeatable probe checks CSE health after each mutation and halts without further calls if it becomes unresponsive; it reports the test path for operator cleanup in that case. The [workout integrity tests](../tools/tests/test_workout_ts_integrity.py) cover the stored CIN shape, lossless TSI construction/readback, and rejection of mismatched IDs, counts, rounds, duration, source, status, and timestamp. The [probe safety tests](../tools/tests/test_onem2m_workout_ts_probe.py) check private callback values are not printed and that a post-create CSE timeout causes immediate stop with no further DELETE. TSI UPDATE was not attempted: TinyIoT treats TSI as immutable, while TS label UPDATE worked. This proves basic storage lifecycle and single-workout integrity, not analytics rollups, high-rate ingestion, retention behavior under load, or authorization with a least-privilege originator. Keep TS outside the production webcam-to-CindyPT flow until those are separately justified and tested.

## Permanent webcam workout TimeSeries (2026-11-11)

The previous *disposable* probe above is separate historical evidence. This follow-up deliberately created the permanent `CindySense_AE/WorkoutPerformance_TS` (`ty=29`) under the existing AE, without touching production containers, ACPs, subscriptions, or workout-summary CINs. Its initial GET was HTTP 404 / RSC 4004. Provisioning POST was HTTP 201 / RSC 2001; readback was HTTP 200 / RSC 2000 with `mni=100`, `mbs=262144`, `cni=0`. A second provision call reused the same TS (HTTP 200 / RSC 2000), without updating it. The count limit matches the existing summary-history scale; the 256 KiB byte cap bounds growth. This is **not** indefinite TSI retention: on the 101st or an oversized history, TinyIoT may remove old TSIs. Original WorkoutSummary CINs remain authoritative and untouched. No `mia` or `et` was added.

The [optional TS mirror](../tools/workout_timeseries.py) reads back the confirmed WorkoutSummary CIN before any TSI write, validates completed webcam status, source, ID, counts, rounds, duration, and CSE creation time, then names the TSI from a stable SHA-256 digest of the workout ID. It writes one TSI (`ty=30`) with that summary CIN's creation time as `dgt` and as `sourceCinCreatedAt` in `con`; this is a **CSE storage timestamp**, not an exercise measurement timestamp. Existing TSI content is compared to the confirmed summary; conflicting content fails closed. A lost POST reply is resolved by readback, while an unexpected HTTP/RSC response fails. The webcam completion path calls this mirror **only after** [SummaryPublisher](../tools/workout_summary.py) has confirmed WorkoutSummary. TS errors are logged separately and do not invalidate the confirmed summary. Repeated notification callbacks do not invoke the TS writer. Explicit, bounded `reconcile` mode can repair missed mirrors after connectivity returns; it is not an automatic background job. Disable the optional mirror with `--no-workout-timeseries` if needed. `--workout-ts-origin` allows a later verified narrow writer.

Live evidence from the existing stored webcam workout `final-summary-097c0172`: source CIN `CindySense_AE/WorkoutSummary/final-summary-db23b5d6aa94419b8454d62bdfc234b1` read back successfully and produced TSI `CindySense_AE/WorkoutPerformance_TS/workout_314c8e533cca49cdb0d1fb898adf6aaa`. First mirror: TSI absence GET HTTP 404 / RSC 4004, TSI POST HTTP 201 / RSC 2001, readback GET HTTP 200 / RSC 2000, with all workout fields and source timestamp validated. Second mirror: existing TSI GET HTTP 200 / RSC 2000, returned `verified` with no POST. Subsequent TS GET was HTTP 200 / RSC 2000 with `cni=1`. TSI GET was HTTP 200 / RSC 2000. The stored source had 5 pull-ups, 10 push-ups, 15 squats, one round, 1200 seconds, and `source=webcam`. This proves a real stored summary can be mirrored idempotently; it does **not** prove an actual new physical webcam completion invoked the updated publisher.

Read-only postcheck: `RepetitionCount`, `WorkoutSummary`, the TSI, both `RepCount_Sub` and `Summary_Sub`, and `WorkoutMonitoring_GRP/fopt` each returned HTTP 200 / RSC 2000; FastAPI snapshot and CindyPT progress each returned HTTP 200. Subscription delivery itself was not retriggered. `CDashboard` and `CCindyPT` TS GETs returned HTTP 200; `CWebcam` AE/summary GETs returned HTTP 403 / RSC 4103 in preflight. Existing summary publishing uses `CAdmin`, so the optional TS writer currently inherits that broad origin unless `--workout-ts-origin` names a verified narrower one. **No ACP was widened or modified.** A dedicated write-only/verify-capable identity remains a security improvement requiring separate ACP impact review.

The new read-only backend endpoint `/api/onem2m/workout-timeseries` has an offline test: three GETs use `CDashboard`, with no writes, and data is confined to the TS subtree. It is **not live yet**: the currently running FastAPI returned HTTP 404 because it has not loaded the changed code. Restart FastAPI during a controlled deployment, then test the endpoint with real data. The API currently has no separate user authentication, so do not expose this history route publicly without access controls. Do not restart TinyIoT solely for this verification. Historical TSI persistence **across a TinyIoT restart is unverified**; the in-process readback above is not a restart test. Offline tests: 19 focused TS/integrity/API tests passed. A simulated TS HTTP 503 leaves the summary fixture unchanged; no real TS failure or physical webcam workout was induced. Remaining gaps: physical completion and notification end-to-end, controlled server-restart persistence, live read-only endpoint, bounded reconciliation under genuine outage, and retention behavior at scale. No ESP32 or detection/audio/UI/CindyPT plan changes were made.

A broader `unittest discover -s tools/tests -q` run failed in unrelated existing sensor-collection tests because this environment denied writes and cleanup inside their temporary directories. This is not a passing full suite and does not establish a regression in TS; the focused 19-test run above passed. `git diff --check` found no whitespace errors (only line-ending warnings). No commit or push was made.

## Read-only advanced-resource follow-up (2026-11-19)

Current TinyIoT GETs returned HTTP 200 / RSC 2000 for `WorkoutMonitoring_GRP`, its `/fopt`, and `WorkoutPerformance_TS`. The group still has three CNT members and fan-out returned three member RSC 2000 responses. The TS has `ty=29`, `mni=100`, `mbs=262144`, and `cni=1`; it contains a previously verified workout-backed TSI. No new workout or TS write was performed. Backend `/api/onem2m/discovery` returned 11 CNT references; a nonexistent-label filter returned zero. This confirms current type/label discovery behavior without changing labels. Existing CNT `mni` values remain 1000 for repetitions and 100 for summaries. No new retention or expiration policy was set.

The running FastAPI process returned HTTP 403 for `/api/onem2m/workout-timeseries`: its `CDashboard` read origin could retrieve the TS but TinyIoT denied `fu=1` discovery of its children (RSC 4103). A read-only `rcn=6&ty=30&lvl=1&lim=101` request returned the stored TSI reference with HTTP 200 / RSC 2000 under the same origin. The candidate backend now uses that bounded child-reference retrieval for this history endpoint. A direct, read-only ASGI request to the changed code returned HTTP 200 with one workout; the already-running FastAPI process has not been restarted and still serves the old code. The endpoint response shape and existing CNT/CIN workout flow are unchanged.

Current read-only ACP checks found `CWebcam` create+retrieve only on the dedicated workout publisher policy, `CCindyPT` and `CDashboard` retrieve-only there, and `CMECBridge` create+retrieve on `MECBridge_ACP`. `CWebcam` was denied retrieval of the repetition subscription; `CIntruder` was denied the repetition container; `CDashboard` was denied `MECBridge_AE`. `CindyPT_ACP` still grants `CCindyPT` all permission bits across its AE, and `Dashboard_ACP` grants `CDashboard` all bits across its AE. Narrowing either policy needs separate authorization and impact testing. The local TinyIoT URL remains HTTP; MEC endpoints are configured for HTTPS but both MEC 011 and MEC 028 read-only checks returned HTTP 401. No live MEC consumption was verified.
