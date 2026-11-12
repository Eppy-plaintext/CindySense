# TinyIoT recovery and historical coaching replay assessment

**Later reliability update:** The recovered server stalled again and accumulated 876 threads, 880 descriptors, and 76 CLOSE_WAIT sockets. It was stopped gracefully to contain resource growth; PostgreSQL remained ready and port 3001 now has no listener. The source inspection and proposed validation are in [tinyiot-http-reliability.md](./tinyiot-http-reliability.md). The successful post-restart reads below describe the earlier healthy interval, not current TinyIoT availability.

## Recovery diagnostics captured before restart

At the later recovery inspection, exactly one TinyIoT process `./server -p 3001` was found (WSL PID 4958; working directory `/home/eppy-04/tinyIoT/source/server`). It was `Sl+`, CPU 0.1%, memory 0.3%, RSS 46,464 KiB, elapsed 1:43:39, with 689 open file descriptors, about 699 threads, and 80 CLOSE-WAIT sockets on port 3001. The listener remained on `0.0.0.0:3001`, but a fresh Windows root GET timed out after 3 seconds with HTTP 000 and no X-M2M-RSC. The process's file limit was 10,240, so the observed descriptor count had not reached that limit.

PostgreSQL 14 was active and `pg_isready` reported port 5432 accepting connections. A `tinyuser`/`tinydb` backend session was visible as idle. A direct SQL query could not be run without an interactive sudo password; database responsiveness beyond readiness is not yet proven. No TinyIoT log file was found in its server directory; stdout and stderr pointed to its original terminal (`/dev/pts/0`), and the PostgreSQL journal had no recent entries. The accumulation of hundreds of threads and retained CLOSE-WAIT sockets points most strongly to TinyIoT connection handling. This is a diagnosis, not a proven source-code defect.

The user authorized a graceful restart with the existing binary and database. The original process was stopped and confirmed gone before a single replacement started. No TinyIoT source change was attempted.

## Recovery and preservation result

SIGTERM stopped PID 4958; it was absent from `ps` and port 3001 had no listener before restart. One replacement `./server -p 3001` was started from the same server directory, PID 22238. Both Windows and WSL root GET returned **HTTP 200 / X-M2M-RSC 2000**. PostgreSQL remained active and ready. The publisher ACP readback returned **200 / 2000**, RI `1-20261112T101707,2420075`, with the full approved `pv` and `pvs` unchanged. Each of the two workout containers and two subscriptions returned **200 / 2000**, retained its backed-up RI, and retained the four migrated `acpi` assignments. RepetitionCount retained `cni=292`, `cbs=65513`; WorkoutSummary retained `cni=4`, `cbs=529`. Both subscriptions retained their original event criteria and notification content type; their callback destinations matched the configured URL in memory and were not printed. No database reset or ACP change occurred.

Three bounded sequential root GETs each returned **200 / 2000**, and the recovered process remained at two threads, six file descriptors, and no CLOSE-WAIT sockets (the `ss` count of one included the header). This short check did not reproduce the earlier accumulation. The installed server's `httpd.c` uses a detached thread per accepted connection and only sets a receive timeout; the earlier 699-thread/80-CLOSE-WAIT condition suggests requests were retained inside the server. Its exact blocking point is unproven. No TinyIoT source was changed. The replacement server's diagnostic stderr log was kept private under the workspace `tmp/` directory and was not copied into evidence because it may contain request data.

## Stored summary and coaching readback after recovery

`WorkoutSummary/la` and `WorkoutSummary?rcn=4` as CCindyPT each returned **HTTP 200 / X-M2M-RSC 2000**. Four CINs were present. The newest CIN, `final-summary-db23b5d6aa94419b8454d62bdfc234b1`, has RI `4-20261111T042142,4950007` and CSE creation time `20261111T042142,495`. Its content says `sessionId=final-summary-097c0172`, `status=completed`, `source=webcam`, one round, 1,200 seconds, five pull-ups, ten push-ups, and fifteen squats. This is a **stored content claim**, not independent proof of a physical session or its actual completion time. The CIN has no `completionReason`, although the current webcam runner's `completed_summary` adds that field for a timer finish or explicit stop. The earlier [feature audit](../onem2m-feature-audit.md) identifies these same counts as test supplied and explicitly says the physical completion was not proven.

The earlier summary CINs were `FinalResult_001` (no workout ID or source), `live-739115a9e38648ef884856e02981cc52` (no source), and `live-fe49355cae3b402299bb85a63ee82864` (source `webcam`, but no `completionReason`). A bounded `RepetitionCount?rcn=4` read as CCindyPT returned **200 / 2000** and embedded 150 of the container's 292 CINs; none of those 150 matched any of the three identified summary session IDs. This does not rule out matches among the 142 instances omitted by the response, but it supplies no corroboration for the latest summary.

`FitnessGoals/la` as CCindyPT returned **200 / 2000** with RI `4-20261107T053717,1730000` and a saved goal labeled `TEST CindyPT integration 2026-11-07`, seven rounds in 1,200 seconds. `AIRecommendations?rcn=4` returned **200 / 2000** and included an existing rule-based CIN, RI `4-20261111T103416,5750053`, whose `sourceWorkoutId` is `final-summary-097c0172`. It has no goal version and was not created through the named durable-job path. The durable SQLite job table was empty, and FastAPI metrics showed zero validated callbacks and no coaching jobs. Thus the workout has an existing recommendation, but **automatic CindyPT processing is not demonstrated**.

No historical callback was sent. The latest summary's physical provenance is unverified, and creating another recommendation for a workout ID already present in AIRecommendations would risk duplication. Historical replay, durable processing, dashboard display of newly generated advice, and live subscription delivery remain **NOT VERIFIED**. No synthetic workout or recommendation was created.

2026-11-12. This attempt was a proposed replay of a previously stored workout, **not a new live subscription event**. No notification was submitted and no oneM2M resource was created, updated, or deleted.

## Read-only results

| Check | Result |
| --- | --- |
| FastAPI `GET /api/integration/metrics` | HTTP 200; `validatedCallbacks=0`, `duplicateCallbacks=0`, `rejectedCallbacks=0`, `coachingJobs={}` |
| TinyIoT summary CIN listing as CCindyPT | Timed out after the client 5-second read timeout; no HTTP response and no X-M2M-RSC |
| TinyIoT root GET from Windows as CAdmin | Timed out after 15 seconds; HTTP 000, no X-M2M-RSC |
| TinyIoT root GET inside WSL as CAdmin | Timed out after 5 seconds; HTTP 000, no X-M2M-RSC |
| TinyIoT process | One `./server -p 3001` process remained present, PID 4958; approximately 577 open file descriptors and 80 CLOSE-WAIT sockets on port 3001 at inspection |

The previously recorded `WorkoutSummary` container count was four CINs, but no CIN representation could be retrieved in this attempt. Therefore no resource ID, workout ID, completion time, exercise counts, rounds, duration, or webcam provenance was established. The latest valid completed historical workout and its prior coaching status are **NOT VERIFIED**. The process being present is not evidence that the CSE is healthy.

## Safety decision

The replay was stopped before sending a callback. The production notification validator in `backend/notifications.py` requires a configured subscription reference, a CIN name and optional RI, then reads that exact CIN from TinyIoT. The durable worker in `backend/coaching_jobs.py` reads it again before writing advice. A replay now would either invent an identity or fail readback, so no historical notification was fabricated. No manual recommendation endpoint was called. No recommendation was written, and no dashboard claim was made.

After TinyIoT is restored, first retrieve and validate the stored CIN and corroborating repetition records. Check the durable job database and any deterministic `coach-` recommendation for the current goal version. Only then build a clearly marked historical replay request for the production callback with the stored CIN name and RI and the existing `Summary_Sub` reference, letting the callback validate by TinyIoT readback. Do not put the callback token in evidence or logs. Repeating that request should leave one durable job and one stored recommendation; verify with readback and dashboard API/browser. This is a future procedure, **not a completed test result**.

## Regression checks

`python -m unittest discover -s backend/tests`: 91 passed. `python -m unittest discover -s tools/tests`: 118 passed. These are mocked/unit tests and do not establish a live historical replay.
