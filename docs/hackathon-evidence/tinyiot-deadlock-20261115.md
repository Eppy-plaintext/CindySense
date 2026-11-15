# TinyIoT HTTP hang: live diagnosis and isolated fix (2026-11-15)

## Observed production failure

The running server was PID 557 on port 3001. It had 1,026 threads: the main thread, a monitoring thread, and 1,024 request workers. The listen queue was 129/128 and local HTTP GETs timed out. A two-second `pidstat` sample showed PID 557 using an average of 100.5% of one CPU core.

Read-only GDB attachment identified a lock cycle:

- LWP 24980 owned `pg_conn_lock` and waited for `main_lock` in `find_rtnode_by_ri` (`rtManager.c:718`), reached through `getNonDiscoverableAcp` while building a PostgreSQL discovery filter in `db_get_descendants` (`postgresql_implement.c:1802` in the original source).
- LWP 24981 owned `main_lock` and waited for `pg_conn_lock` in `db_get_resource_by_uri` (`postgresql_implement.c:668`), reached from `find_rtnode_by_rn` (`rtManager.c:451`) while looking up a CIN.
- The main thread was spinning in `serve_forever` at original `httpd.c:99`, scanning for an empty slot after all 1,024 request slots filled. The original accept loop also ignored `pthread_create` failure and detached each worker only after it finished.

The deadlock is the initiating cause of the hang. Unbounded worker accumulation and the full-slot spin amplified it. PostgreSQL was reachable and idle; it was not the blocking component.

## Code changes

The [patch](./tinyiot-deadlock-fix.patch) was applied to the existing, already locally modified TinyIoT source. It changes only `httpd.c` and `postgresql_implement.c`:

- Resolve discovery ACP and forbidden URI lists before taking `pg_conn_lock`; keep SQL escaping and queries under the PostgreSQL lock. This removes the observed `pg_conn_lock` → `main_lock` acquisition path without weakening discovery permissions.
- Cap active HTTP workers at 64, return 503 when saturated, handle thread creation failures, and detach workers at creation. Initialize `accept` address length and apply receive/send timeouts only to valid sockets.
- Send responses with `MSG_NOSIGNAL` and handle partial writes. A disconnected-client probe against the first minimal build exposed a SIGPIPE exit before this response fix; the final build passed that probe.

The production source files are `/home/eppy-04/tinyIoT/source/server/httpd.c` and `/home/eppy-04/tinyIoT/source/server/postgresql_implement.c`. The production `server` executable was **not** rebuilt or replaced. Its SHA-256 remains `8bce4692af5a202ba5f6fddd2df182e12385481ef189614fd29ed06d58fbfbef`.

## Isolated verification

The exact patched C files were copied from the production source baseline into `/home/eppy-04/tinyIoT-prod-minimal-fix-20261115`. Only its `config.h` was changed for the isolated PostgreSQL cluster on 127.0.0.1:55432 and database `cindysense_phase6_test`. The test server listened on port 3101. The full 20-minute run used the updated [endurance probe](../../tools/onem2m_isolated_endurance.py), which verifies the isolated executable path when production port 3001 is active.

- `make -j2`: passed. Focused [protocol probe](../../tools/onem2m_isolated_reliability_probe.py): passed, including concurrent GETs, partial request timeout, disconnected client, CIN, ACP, subscription, group, and discovery checks.
- 1,200-second run: 1,200/1,200 isolated CIN writes, 600/600 container reads, and 600/600 concurrent discovery/CIN lookup pairs; zero failures. Median latency 15.48 ms, p95 50.27 ms, maximum 97.55 ms across 3,005 requests.
- Every minute sample and final reading: 2 threads, 8 descriptors, zero `CLOSE_WAIT`; PostgreSQL ready. RSS rose from 12,884 KiB to 14,160 KiB. Disposable AE cleanup returned HTTP 200/RSC 2002; subsequent GET returned HTTP 404/RSC 4004. Isolated database had zero AEs after cleanup.
- The isolated process averaged 0.5% of one CPU core during a two-second `pidstat` sample, while production PID 557 averaged 100.5%.
- `patch --dry-run -p1` against the production source passed before applying; the production C files then matched the tested isolated C files byte-for-byte. `git diff --check` passed.
- A separate full rebuild with production database configuration passed in `/home/eppy-04/tinyIoT-prod-build-20261115`; staged binary SHA-256 is `608574dbc56af1108419ed50bfa7f58e56e0153663061c5745a607377b6fe3be`. It was not started or deployed.

The isolated server and PostgreSQL cluster were stopped. Port 3101 and port 55432 no longer listen. PID 557 and production PostgreSQL on port 5432 remain untouched. No production resources, CINs, ACPs, subscriptions, or retention settings were changed.

## Deployment at a maintenance window

1. Quiesce CindySense traffic and prevent a workout from starting. Take and verify a fresh full `tinydb` backup, and retain a copy of the current production executable for rollback.
2. Confirm the staged production binary hash above and `config.h` values `PG_PORT 5432` and `PG_DBNAME "tinydb"`. Do **not** deploy the isolated test binary, which targets port 55432.
3. Since the old process is deadlocked, its `SIGINT` shutdown handler may itself wait on `pg_conn_lock`. During the maintenance window, terminate only the verified old PID with `SIGTERM`, wait for it to exit, and confirm port 3001 has no listener. Do not signal PostgreSQL.
4. Atomically install the staged production binary into `/home/eppy-04/tinyIoT/source/server/server`, keeping the original as a rollback copy. Start `./server -p 3001` from the normal server directory.
5. Verify repeated authorized GETs, `/api/health`, `/api/workout/snapshot`, thread count, CPU, and the existing resource/ACP/subscription inventory. Roll back to the saved executable if these checks fail. No database migration is required.

The 20-minute isolated test covers the representative cadence and the observed lock-cycle request mix. It does not prove behavior under every notification destination failure or shutdown race. `stop_server` joins the monitoring thread but does not drain HTTP workers before closing the database; this separate shutdown limitation remains.
