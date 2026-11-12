# TinyIoT HTTP reliability investigation (2026-11-12)

The observations below describe the earlier production investigation. The Phase 6 isolated repair and its results follow at the end. No production TinyIoT source, executable, database, ACP, subscription, or workout resource was modified.

## Observed recurrence and containment

The recovered TinyIoT process initially had two threads, six file descriptors, and no CLOSE_WAIT sockets; Windows and WSL root GETs returned HTTP **200 / X-M2M-RSC 2000**. Later, without a stress test, the same PID 22238 reached 814 threads, 818 descriptors, and 78 CLOSE_WAIT sockets. A subsequent root GET timed out after three seconds with HTTP 000 and no X-M2M-RSC. At a later snapshot it had 876 threads, 880 descriptors, and 76 CLOSE_WAIT sockets. `awk` over `/proc/22238/task/*/wchan` found 839 threads waiting in `futex_wait_queue`, one accepting connections, and one sleeping. This is consistent with many HTTP workers waiting on a shared lock; the exact mutex or lock owner was not identified. The former process had exhibited approximately 699 threads, 689 descriptors, and 80 CLOSE_WAIT sockets before its graceful restart.

Windows showed matching FIN_WAIT2 sockets owned by the FastAPI Python process and the WSL relay process, corresponding to CLOSE_WAIT sockets at TinyIoT. PostgreSQL 14 remained active and `pg_isready` accepted connections; existing resource reads had succeeded after the restart. These facts point to a stalled TinyIoT request path, not a primary PostgreSQL outage or a failure of Windows-to-WSL routing. They do not prove why a TinyIoT lock was held. The replacement was stopped with SIGTERM after the second stall; PID 22238 exited and port 3001 has no listener. PostgreSQL remained ready. TinyIoT is **not currently available**; leaving the repeatedly stalled server running would continue to accumulate workers.

## Source inspection

In the installed WSL `~/tinyIoT/source/server/httpd.c`, `serve_forever` accepts each socket into a slot and starts a new `respond_thread`. A normal worker calls `respond`, closes its accepted socket, sets the slot to -1, and detaches itself. Thus a worker that reaches its return path closes the socket, but a worker blocked in `respond` retains it and produces CLOSE_WAIT after its peer closes. `pthread_create` return status is ignored; failure can also leave an accepted socket and occupied slot without a worker to close it. Detachment occurs only at the end of the worker, so stalled workers remain joinable and consume resources.

The accepted socket has `SO_RCVTIMEO=3` seconds, but no send timeout. That timeout bounds `recv`, not `route`, database calls, mutex acquisition, or response writes. The repeated root GETs sent complete requests yet timed out waiting for a response. `MAX_CONNECTIONS=1024` bounds slots, but does not prevent hundreds of retained workers. The accept address length is initialized to zero rather than `sizeof(clientaddr)`, an unrelated correctness concern that should be separately tested.

In `postgresql_implement.c`, `db_commit_tx` returns on a failed `COMMIT` without clearing transaction ownership or unlocking `pg_conn_lock`; `db_rollback_tx` has a similar failed-`ROLLBACK` path. This is a concrete potential permanent lock leak if PostgreSQL returns an error. No failure of either transaction was observed in logs, so it is **not confirmed as this incident's cause**. The server also has a global `main_lock`; the futex snapshot does not distinguish it from the PostgreSQL lock. Debug logging uses a shared buffer, another concurrency risk, but no corruption was verified.

## Minimum proposed patch and validation plan

The proposed first patch was to guarantee transaction-state cleanup and `pg_unlock()` on both failed COMMIT and failed ROLLBACK paths, check `pthread_create` and close/release accepted sockets on failure, detach workers immediately, and bound response sends. These proposals have now been implemented and tested in the isolated copy below.

The prior inspection did not identify the lock owner in the production stall. The isolated tests below verify specific failure paths; they do not reproduce or prove the original stall's exact cause.

## Phase 6 isolated repair (2026-11-12)

### Isolation and preservation

- The CindySense production TinyIoT endpoint on port **3001** remained stopped. A pre-existing process using the original executable was observed listening on port **3000** throughout this work (PID 43531, started before the isolated setup); it was not started, queried, modified, or stopped here. Its relationship to the production database should be checked before any future deployment. The original executable at `~/tinyIoT/source/server/server` remains SHA-256 `8bce4692af5a202ba5f6fddd2df182e12385481ef189614fd29ed06d58fbfbef`. A separate byte-identical backup is `~/tinyIoT-phase6/production-server-original`.
- All patching/builds were in `~/tinyIoT-phase6/server`. Its configuration selects port **55432** and database **cindysense_phase6_test**, hosted by a separate PostgreSQL 14 cluster at `~/tinyIoT-phase6/pgdata`. The test CSE listened on port **3101**. The production PostgreSQL cluster and database were never used for tests. An initial attempt to create a test database in the production cluster was rejected for lack of CREATEDB privilege; no database was created there.
- The complete reviewable diff is [tinyiot-phase6.patch](./tinyiot-phase6.patch). It covers only `httpd.c`, `postgresql_implement.c`, and `resources/cin.c`. The test-only database/port configuration is deliberately excluded from the production patch. Patched isolated executable SHA-256: `d5a7a63ae0324039d63879476dc1cf2f504475fe76a0bfaa520924b096cf9792`. `patch --dry-run -p1` against the untouched production source checked all three files successfully; it did not apply the patch.

### Confirmed defects and exact changes

| Source | Confirmed defect | Isolated fix |
| --- | --- | --- |
| `postgresql_implement.c` | Failed `COMMIT` and `ROLLBACK` returned without releasing the shared PostgreSQL mutex/transaction ownership. `COMMIT` with zero depth or a missing connection also left the lock held. | Reset transaction state and unlock on every terminal path; discard the broken `PGconn` after failed completion; guard null `PGresult`. A forced deferred-COMMIT failure showed the original code would enter the leaking branch. |
| `httpd.c` | `pthread_create` failure was unchecked; accepted fd and slot could remain occupied. Workers detached only after finishing, and response `write()` could terminate the whole process with SIGPIPE on client disconnect. No send timeout existed. | Check creation, close/release on failure, detach immediately, limit active workers to 64 with HTTP 503 / RSC 5000 at capacity, set receive/send timeouts, use `MSG_NOSIGNAL` and checked complete sends, and initialize accept address length. |
| `resources/cin.c` | Fault injection exposed a double free when a CIN transaction failed: `free_rtnode` freed the CIN JSON still owned by `root`, then `cJSON_Delete(root)` freed it again. The failed transaction also left container counters advanced in memory. | Detach CIN JSON before freeing the RTNode on all four failure exits; restore `cni`, `cbs`, and `st` when the transaction rolls back or COMMIT fails. |

The observed production futex/CLOSE_WAIT stall is consistent with the proven lock leak, but no production failed-COMMIT/ROLLBACK log or mutex-owner stack was captured. The precise cause of that incident remains **unconfirmed**. The worker cap limits accumulation if another lock stalls; it does not remove a stall. Failed `ROLLBACK`, a real PostgreSQL disconnect, and CIN eviction during a failed transaction have not been separately fault-injected.

### Bounded isolated verification

Detailed oneM2M operation statuses are in [tinyiot-phase6-isolated-results.json](./tinyiot-phase6-isolated-results.json). The probe [onem2m_isolated_reliability_probe.py](../../tools/onem2m_isolated_reliability_probe.py) refuses the production port. All created AE/ACP/CNT/CIN/SUB/GRP test resources were under a unique disposable AE and deleted; a subsequent GET returned HTTP **404 / RSC 4004**.

| Test | Observed result |
| --- | --- |
| Root GET | 10 sequential and 24 GETs with maximum eight concurrent: each HTTP **200 / RSC 2000**; final GET also **200 / 2000**. |
| Partial header | Receive timeout: HTTP **408 / RSC 4008**. |
| Client disconnect | Initial patch crashed on response SIGPIPE; after checked `MSG_NOSIGNAL` sends, subsequent root GET: HTTP **200 / RSC 2000**. |
| Worker saturation | At 64 deliberately held partial requests: 66 threads/70 fds including baseline threads; 65th request: HTTP **503 / RSC 5000**. After four seconds: 2 threads/6 fds, zero isolated CLOSE_WAIT. Baseline was 2 threads/6 fds. |
| Forced deferred COMMIT failure on isolated CIN table | CIN POST: HTTP **500 / RSC 5000**; subsequent root GET: **200 / 2000**; container `cni=0`, `cbs=0`, `st=0`; disposable AE cleanup DELETE: **200 / 2002**. Fault trigger/function were dropped. Before the CIN fix, this test crashed the isolated CSE. |
| Representative oneM2M lifecycle | AE, ACP, two CNT, CIN, SUB, GRP create/readback; GRP `/fopt`; type and label discovery: successful with expected HTTP/RSC. Scoped ACP denied GET and POST: **403 / 4103**. SUB delivery to a receiver was **not** exercised; SUB create/readback was. |
| After full probe | 2 threads, 6 descriptors, zero isolated CLOSE_WAIT; production port 3001 had no listener. |
| Repository regression | Backend **91/91**, tools **118/118** passed; `git diff --check` passed (line-ending notices only). |

### Deployment decision and rollback preparation

The patch is **ready for source review and further staging tests, not yet established as production-ready**. The exact original mutex owner was not captured, and failed-ROLLBACK/connection-loss plus notification delivery under the patched server remain unverified. No patched executable has been installed in production and no production server was started by this work. The isolated CSE and isolated PostgreSQL cluster were stopped after testing; the pre-existing port 3000 process was left untouched.

Before a separately authorized deployment: review [tinyiot-phase6.patch](./tinyiot-phase6.patch), repeat isolated fault tests including failed ROLLBACK and callback delivery, identify the purpose and database of the pre-existing port 3000 process, make a fresh production database backup and resource/ACP/subscription snapshot, verify the original binary SHA, build with the existing production database configuration, and perform a controlled maintenance-window swap. Preserve `~/tinyIoT-phase6/production-server-original`. Rollback would stop the patched process, restore the byte-identical original executable, and restart exactly one intended server against the unchanged database, then verify HTTP/RSC and resource IDs. **These production steps have not been performed.**

## Phase 7 isolated staging verification (2026-11-12)

### Existing process and isolation check

The WSL process on port **3000** is PID **43531**, launched as `./server` from `~/tinyIoT/source/server`, using the unpatched original executable. `ss` showed its established connection from local port 51782 to PostgreSQL **5432**; the matching PostgreSQL backend (PID 43532) identified the session as `tinyuser tinydb`, idle. Thus this process **is connected to the production database**. It predated Phase 7 and was neither queried through HTTP nor stopped, restarted, or modified. Port **3001** had no listener. The patched isolated CSE ran on **3101** against the independent PostgreSQL cluster on **55432** and database `cindysense_phase6_test`. Only these isolated services were restarted for staging.

### Transaction and database failure checks

An isolated `AFTER INSERT` trigger on the test `cin` table slept during a disposable CIN write. A second connection terminated the **isolated** TinyIoT PostgreSQL backend while it was inside the transaction. The isolated CSE log recorded `Failed to insert resource: FATAL: terminating connection due to administrator command`, followed by `Failed to rollback transaction: no connection to the server`. Results: CIN POST HTTP **500 / X-M2M-RSC 5000**; subsequent root and container GETs **200 / 2000**; failed CIN GET **404 / 4004**; container `cni=0`, `cbs=0`, `st=0`; disposable AE deletion **200 / 2002**. The fault trigger and function were dropped. This directly exercised the failed-ROLLBACK cleanup branch and showed the mutex was released, because subsequent requests completed.

Separately, the isolated PostgreSQL cluster was stopped while the isolated CSE stayed up. An attempted disposable AE POST returned HTTP **500 / RSC 5000** (`DB store fail`). After the cluster restarted, root GET returned **200 / 2000**, the failed AE was absent (**404 / 4004**), and creating the same AE succeeded (**201 / 2001**), followed by readback **200 / 2000** and cleanup **200 / 2002**. No production database was stopped or queried for this fault test.

### Subscription callback and duplicate delivery

A temporary FastAPI receiver listened on Windows port **8101**, reachable from the isolated WSL CSE through the WSL host gateway. The disposable AE, container, publisher ACP, and subscription each returned **201 / 2001**; subscription readback returned **200 / 2000**. The isolated subscription targeted only the temporary receiver and subscribed to direct-child creation (`net=3`). TinyIoT sent a verification callback (`vrq=true`, `sur=/tinyiot/23-20261112T125706,7010013`, no event payload). After assigning the scoped ACP (**200 / 2004**), `CWebcam` read the container (**200 / 2000**) and published one CIN (**201 / 2001**); `CDenied` POST was rejected (**403 / 4103**).

The second callback carried `net=3`, the same `sur`, CIN `ri=4-20261112T125706,7890014`, and parent `pi=3-20261112T125706,6670011`; these matched the stored subscription, CIN, and container readbacks. Its isolated content marker was `phase7-isolated`. A manual repeat HTTP POST of the exact callback to the temporary receiver returned **200 / 2000**; its in-memory identity check retained one unique CIN and marked the repeat duplicate. **TinyIoT retry behavior and production FastAPI/CindyPT idempotency were not tested by this manual repeat.** Disposable AE deletion returned **200 / 2002** and absent readback **404 / 4004**. No production URL, token, subscription, or workout container was used.

### Compatibility and deployment preparation

Read-only schema metadata comparison of `information_schema.columns` found **234 columns in each** of production `tinydb` and isolated `cindysense_phase6_test`, with **zero differing table/column/type/nullability entries**. This supports schema compatibility but does not prove compatibility with every production record or migration history. The patch again passed `patch --dry-run -p1` against the untouched original source (`httpd.c`, `postgresql_implement.c`, `resources/cin.c`). The original production executable and its separate backup both retained SHA-256 `8bce4692af5a202ba5f6fddd2df182e12385481ef189614fd29ed06d58fbfbef`; the isolated patched binary retained SHA-256 `d5a7a63ae0324039d63879476dc1cf2f504475fe76a0bfaa520924b096cf9792`.

Deployment checklist for a later, separately approved maintenance window: identify and coordinate the existing port 3000 process; snapshot the production database and resource IDs/ACPs/subscriptions; confirm exactly which port and database the intended service should use; review the three-file patch; rebuild with the unchanged production configuration; preserve and hash the original binary; stop only the identified target instance; deploy one patched instance; check HTTP/RSC, key resource IDs, ACP denial/allow paths, subscription callback, and workout readback. Rollback: stop only that patched instance, restore the byte-identical original executable, restart the one intended service, and verify the same resources and permissions. **No deployment or rollback occurred in Phase 7.**

### Full 20-minute bounded cadence

The [isolated endurance probe](../../tools/onem2m_isolated_endurance.py) ran **1200.0 seconds** on port 3101. It used a unique disposable AE and scoped ACP: `CWebcam` created one isolated CIN per second, modeling the webcam publisher's configured 1-second pose interval, while it read the container every two seconds, modeling the dashboard's configured 2-second snapshot poll. It refused a port 3001 listener and stopped on excessive threads, descriptors, CLOSE_WAIT, memory growth, or lost test PostgreSQL readiness. This was an isolated workload model, not a real workout or production notification chain.

| Measurement | Observed |
| --- | --- |
| CIN writes | **1200/1200** HTTP **201 / RSC 2001**; 0 failed |
| Container reads | **600/600** HTTP **200 / RSC 2000**; 0 failed |
| Request latency | Median **20.82 ms**, p95 **60.33 ms**, maximum **152.98 ms** across 1805 requests including setup |
| Threads and descriptors | Baseline **2 / 6**; all 19 minute samples and final **2 / 6** |
| CLOSE_WAIT | **0** at baseline, every minute sample, and final |
| Server RSS | **8448 KiB** baseline; maximum sampled **9196 KiB** (+748 KiB); **9172 KiB** final (+724 KiB) |
| Test PostgreSQL | Ready at every minute sample and final |
| Cleanup | AE DELETE HTTP **200 / RSC 2002**; absent GET **404 / 4004**; test DB has **0** Phase 6/7 AEs, CINs, SUBs, and fault triggers |
| Final isolated CSE check | Root GET HTTP **200 / RSC 2000** |

The complete minute samples and result counters are in [tinyiot-phase7-endurance-results.json](./tinyiot-phase7-endurance-results.json). This sustained run did not reproduce the prior stall at the modeled cadence. It does not prove safety under other request mixes, higher concurrency, or a failing production database.

After staging, backend unit tests passed **91/91**, tool tests **118/118**, and the focused mocked coaching/job tests **4/4**. `git diff --check` found no whitespace errors. The isolated CSE and PostgreSQL cluster were stopped; final listeners were the unchanged original-binary process on port 3000 and production PostgreSQL on 5432, with no listeners on 3001, 3101, or 55432.

### Phase 7 readiness conclusion

The failed-ROLLBACK branch, database outage/recovery, live isolated verification and notification callback, and full cadence run **passed**. The three-file patch is ready for a reviewed deployment rehearsal, but **not yet approved or proven for production deployment**. The original production mutex owner was never captured; failed transactions involving CIN retention/eviction remain untested; and the pre-existing port 3000 process is connected to production `tinydb`. Those uncertainties and the production backup/one-instance checklist above must be resolved before a separately authorized deployment. Phase 7 did not start port 3001, deploy a binary, modify production resources, or push code.

## Phase 8 production deployment readiness review (2026-11-12)

### Process ownership, routing, and database

- The original unpatched WSL TinyIoT executable is still PID **43531**, launched by an interactive shell from `~/tinyIoT/source/server` and listening on **0.0.0.0:3000**. Its PostgreSQL connection is to production `tinydb` on 127.0.0.1:5432. At inspection it had two threads and approximately 16,444 KiB RSS. Windows also has a `wslrelay.exe` listener on 127.0.0.1:3000. No client was attached to the WSL port 3000 socket at the instant sampled; this does **not** establish that another application never uses it. Its owner/purpose must be confirmed before any shutdown. No WSL server listened on port **3001**.
- Windows portproxy maps **0.0.0.0:3001** to the current WSL address at port **3001**. Windows FastAPI on port **8000** had connections to that Windows portproxy; the React dev server was on **5173**. Backend `.env` selects `http://127.0.0.1:3001/TinyIoT`; `backend/config.py` has a port 3000 fallback, and `tools/webcam_tinyiot.py` also defaults to port 3000 without an explicit endpoint. Thus the deployed webcam must be given the intended 3001 URL explicitly, and the backend environment must be verified before startup. Do not assume a successful Windows TCP connection to the portproxy means a WSL CSE is listening.
- Production PostgreSQL 14.24 on **5432** was ready. `tinydb` was approximately **10,603,867 bytes** with 20 tables. `pg_dump` and `pg_restore` 14.24 are installed; roughly 953 GB disk space was free at inspection. A read-only `pg_dump -Fc --schema-only --no-owner --no-acl` archive listed **44** entries and restored successfully with `--single-transaction --exit-on-error` into a disposable database on the **isolated** port 55432 (20 tables); that isolated restore database was then dropped. **No full production data backup was taken during Phase 8**. Backup tooling is verified, but the required fresh full backup and restore rehearsal remain a deployment prerequisite.
- Read-only schema comparison between production `tinydb` and the isolated test database found **234/234 columns, 2/2 indexes, 20/20 constraints, and 0/0 noninternal triggers** matching. This supports binary/schema compatibility, not behavior on every historical record. Running both port 3000 and a new port 3001 process against the same production database risks duplicate notifications, conflicting in-memory resource trees/counters, and lost or contradictory writes. Never do this during cutover.

### Production identity and configuration baseline

The following SQL metadata checks were read-only. Resource IDs and `acpi` assignments matched the existing ACP backups and migration record. Existing subscription destinations matched backend configuration **in memory**; the callback URI and token were not printed or copied into this report.

| Production resource | Resource ID | Assignment / baseline |
| --- | --- | --- |
| `CindySense_AE` | `CAdmin` | `CindySense_ACP` |
| `CindySense_ACP` | `1-20261026T160317,2210029` | Existing policy unchanged |
| `CindyPT_ACP` | `1-20261026T160425,6490030` | Existing policy unchanged |
| `Dashboard_ACP` | `1-20261026T160433,5250031` | Existing policy unchanged |
| `CindySense_WebcamPublisher_ACP` | `1-20261112T101707,2420075` | Scoped publisher policy |
| `RepetitionCount` | `3-20261026T145138,5460005` | Publisher ACP; `cni=292`, `cbs=65513`, `mni=1000` |
| `WorkoutSummary` | `3-20261026T145138,5610007` | Publisher ACP; `cni=4`, `cbs=529`, `mni=100` |
| `RepCount_Sub` | `23-20261026T161859,4040032` | CindySense ACP; `enc.net=[3]`, `nct=1` |
| `Summary_Sub` | `23-20261026T162232,8210035` | CindySense ACP; `enc.net=[3]`, `nct=1` |
| `CindyPT_AE` | `CCindyPT` | CindyPT ACP |
| `AIRecommendations` | `3-20261026T151037,2440024` | Existing container, `cni=4` |
| `FitnessGoals` | `3-20261026T151037,2010018` | Existing container, `cni=1` |
| `Dashboard_AE` | `CDashboard` | Dashboard ACP |
| `Webcam_AE` | `CWebcam` | Existing AE |
| `WorkoutMonitoring_GRP` | `9-20261111T032431,2490006` | Existing group |

The prior representation backups are [acp-production-backup-20261112.json](./acp-production-backup-20261112.json) and [acp-subscription-assignments-backup-20261112.json](./acp-subscription-assignments-backup-20261112.json). They are baseline evidence, **not** substitutes for a new pre-deployment backup. Avoid exporting subscription `nu` values to a shared evidence bundle.

### Additional transaction defect found in isolated review

The Phase 6 patch passed Phase 7, but an isolated connection-loss fault exposed a further data-integrity defect. `get_pg_conn()` could reconnect *inside* an interrupted transaction, and `update_cnt_cin()` ignored a failed container database update. With an isolated trigger delaying `UPDATE cnt`, terminating only the isolated PostgreSQL backend made CIN POST falsely return HTTP **201 / X-M2M-RSC 2001**. Its in-memory container and CIN appeared updated, while direct SQL showed persisted `cni=0`, `cbs=0`, `st=0`. This was a **confirmed defect**, not merely a hypothesis about the old production stall.

The new [tinyiot-phase8-candidate.patch](./tinyiot-phase8-candidate.patch) includes all Phase 6 fixes plus two minimal changes: `get_pg_conn()` refuses to reconnect while `pg_tx_depth>0`, leaving the transaction owner to fail and unwind; `update_cnt_cin()` propagates `db_update_resource` failure. Only the isolated source tree was edited. The original [tinyiot-phase6.patch](./tinyiot-phase6.patch) remains intact for comparison. The Phase 8 patch changes four files (`httpd.c`, `postgresql_implement.c`, `onem2m.c`, `resources/cin.c`), contains no test database configuration, and passes `patch --dry-run -p1` against the untouched original source. A clean isolated rebuild produced SHA-256 **`95f5f4bf1ba7473af74a24abdfe0bd8533dc6a2986b8afb15a5305332991dff7`**, identical to the incrementally built, fault-tested binary. The original production executable and its separate backup remain SHA-256 **`8bce4692af5a202ba5f6fddd2df182e12385481ef189614fd29ed06d58fbfbef`**.

| Isolated test after Phase 8 fix | Exact observed result |
| --- | --- |
| Mid-transaction PostgreSQL connection termination during CNT update | CIN POST **500 / 5000**; CNT GET **200 / 2000**, counters `0/0/0` in memory and SQL; failed CIN GET **404 / 4004**; disposable cleanup DELETE **200 / 2002**. |
| Failed ROLLBACK after isolated backend termination | CIN POST **500 / 5000**; subsequent CSE/root and CNT GET **200 / 2000**; failed CIN absent **404 / 4004**; cleanup **200 / 2002**. |
| MNI=1 retention with deferred COMMIT failure | CIN POST **500 / 5000**; old CIN and `/la` remained **200 / 2000** with same RI; new CIN **404 / 4004**; CNT counters stayed `cni=1,cbs=3,st=1`. After removing the fault, a new CIN POST **201 / 2001**, counters `1/3/3`, old CIN **404 / 4004**, new CIN **200 / 2000**; cleanup **200 / 2002**, absent GET **404 / 4004**. |
| Representative oneM2M probe | **36** operation records passed across disposable GRP/fanOutPoint, discovery, ACP, SUB representation, and CIN operations. |
| Temporary subscription receiver | SUB create **201 / 2001** and verification callback received; CWebcam CIN POST **201 / 2001** with matching notification CIN/subscription references; denied origin POST **403 / 4103**; repeated callback recognized as a duplicate by the temporary receiver; cleanup **200 / 2002**, absent **404 / 4004**. This does not verify CSE automatic retries or production coaching. |
| Repository regression | Backend **91/91**, tools **118/118** passed after the isolated source fix. |

The Phase 7 20-minute endurance results apply to the earlier three-file binary. The new Phase 8 binary has passed fault and representative protocol checks, but **has not had another 20-minute endurance run**. The original production stall's exact mutex owner remains unknown. MIA expiry during a failed transaction, slow/unreachable notification destinations, shutdown with active requests, and prolonged real production traffic remain untested. The static 64-worker limit protects resources but can return **503 / 5000** if requests pile up; it does not cure an unrelated deadlock.

### Deployment decision and gated checklist — not executed

**Decision:** the four-file candidate is buildable and passed isolated fault checks, but production deployment is **not yet cleared**. Resolve the port 3000 process's ownership/dependencies, review the new patch and remaining failure modes, and complete a fresh full backup/restore rehearsal before approving a maintenance window. The following is the proposed sequence, not an action already taken.

1. Obtain explicit deployment approval and the port 3000 service owner's agreement to stop it if it is a production peer. Identify every client of port 3000; record PIDs, executable hashes, listening sockets, PostgreSQL backend sessions, and the intended single endpoint. Freeze webcam publishing and other CSE writers. Reject cutover if the process's role remains uncertain.
2. Capture a **fresh** production resource snapshot: the IDs, ACP representations/assignments, CNT counters, GRP member list, SUB event criteria and destination hashes/secure private copies, plus database version/schema. Keep private callback destinations and credentials in restricted storage, outside presentation logs. Compare with the baseline table above and the ACP migration backups; stop on a mismatch.
3. Create a full `pg_dump -Fc --no-owner --no-acl` backup of production `tinydb` through the existing local PostgreSQL connection, with restrictive permissions (`umask 077`). Verify archive listing with `pg_restore --list`, copy it to a protected location, and rehearse `pg_restore --single-transaction --exit-on-error` into a **new isolated database**. Verify table/resource counts and representative RIs there. Do not drop or rewrite production `tinydb`.
4. Confirm the original binary hash above and retain both the untouched original executable and `~/tinyIoT-phase6/production-server-original`. Apply the reviewed [Phase 8 candidate patch](./tinyiot-phase8-candidate.patch) to a **separate clean copy** of the original source, build with the unchanged production configuration, and hash the candidate. Compare build flags and port/database settings. Do not overwrite the original source or running executable in this preparation step.
5. In the approved window, stop FastAPI or pause its CSE polling, stop webcam and any other CSE writers, then gracefully stop **only identified** TinyIoT process(es), including port 3000 only if its owner authorizes it. Confirm each original PID exits and ports 3000/3001 have no WSL TinyIoT listener. Never start the candidate while another process is attached to production `tinydb`.
6. Atomically install the reviewed production-configured binary at the approved executable path while retaining the original backup. Start **exactly one** TinyIoT process on the intended port **3001** using the existing production PostgreSQL configuration. Check WSL listening socket and database session; confirm Windows portproxy targets the live WSL IP and that Windows-to-WSL GET works. Keep the old port 3000 process stopped until a rollback decision is made.
7. Perform read-only root GET from WSL and Windows; require HTTP **200 / X-M2M-RSC 2000**. Read back the tabled resource IDs, `acpi`, CNT counters, subscriptions, callback destination hashes, and GRP membership. Check CWebcam/CCindyPT/CDashboard authorized GETs and CWebcam denied subscription GETs. Verify FastAPI is configured for port 3001, then resume it. Start webcam only with an **explicit** port 3001 endpoint; do not rely on the port 3000 fallback.
8. Run short controlled functions **only after read-only checks pass**: use an approved disposable subtree for a CWebcam CIN create/read/delete, ACP denial, GRP/fanOutPoint and discovery, and a temporary callback destination for subscription verification. Do not write synthetic records to historical workout containers or alter existing subscriptions. Check worker/fd/CLOSE_WAIT and PostgreSQL health throughout. Real workout and automatic CindyPT validation require a separately coordinated live session.
9. Trigger rollback for startup/build mismatch, unexpected HTTP/RSC, changed/missing resource IDs or assignments/destinations, failed database health, stuck workers/growing descriptors/CLOSE_WAIT, duplicate CSE instances, or failed controlled operations. Stop writers/FastAPI polling, gracefully stop the **patched** PID, verify exit, restore the original byte-identical executable, and restart the one previously approved original service with its original configuration. Verify root **200 / 2000**, baseline RIs/permissions/subscriptions and database health. Preserve logs and snapshots. **Do not blindly restore the database backup after accepted writes**; first reconcile writes made during the window and get a separate data-recovery decision. Never run both binaries simultaneously against production `tinydb`.

The commands below are a **runbook for a later approved window**, not commands executed in Phase 8. Run them inside WSL and replace the timestamp and approved PID with values captured in that window. Use a secured directory accessible to the PostgreSQL service account for the archive. The candidate must first be built from a clean original-source copy with the unchanged production `config.h`; `-p 3001` selects the intended listener at launch. Validate the candidate hash against its reviewed build record before installation.

```bash
# Before stopping any process, after identifying the port 3000 owner and freezing writers:
pg_isready -h 127.0.0.1 -p 5432
sudo -u postgres sh -c 'umask 077; pg_dump -Fc --no-owner --no-acl -d tinydb -f /SECURE/BACKUP/tinydb-PREDEPLOY.dump'
sudo -u postgres pg_restore --list /SECURE/BACKUP/tinydb-PREDEPLOY.dump
# Restore to a new isolated database for a full-data rehearsal; never target tinydb.

# In the approved maintenance window, after FastAPI polling and all CSE writers stop:
kill -TERM "$APPROVED_ORIGINAL_PID"
# Wait and confirm exit; do not force-kill or continue if it remains alive.
ss -ltnp
sha256sum /home/eppy-04/tinyIoT/source/server/server
cp -p /home/eppy-04/tinyIoT/source/server/server /SECURE/BACKUP/server-original
install -m 0755 /REVIEWED/PRODUCTION/BUILD/server /home/eppy-04/tinyIoT/source/server/server.phase8-next
sha256sum /home/eppy-04/tinyIoT/source/server/server.phase8-next
mv -f -T /home/eppy-04/tinyIoT/source/server/server.phase8-next /home/eppy-04/tinyIoT/source/server/server
cd /home/eppy-04/tinyIoT/source/server
./server -p 3001
```

The final `./server` command is foreground by design: use the existing approved process supervisor/session procedure, record the new PID, and verify there is only one `tinydb` CSE connection. Do not use this snippet unmodified or launch it in a second terminal while an old server remains. Rollback, after stopping and confirming exit of the patched PID, is `install -m 0755 /SECURE/BACKUP/server-original /home/eppy-04/tinyIoT/source/server/server`, confirm the original SHA above, then start the **one approved original listener** with its original port and configuration. A database restore is a separate exceptional decision, not part of routine binary rollback.

No Phase 8 production process was stopped, started, replaced, or queried through a mutating API. No production database records, ACPs, subscriptions, workouts, or credentials were changed. No patch was deployed or pushed.
