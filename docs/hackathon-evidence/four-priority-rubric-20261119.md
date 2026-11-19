# Four-priority rubric evidence — 2026-11-19

## Implementation summary and scope

The working tree already contained uncommitted backend, webcam, dashboard, and MCP work when this pass began. Those files were preserved. This pass changed only the three files listed below. No production CSE resource, ACP, credential, webcam algorithm, or workout event schema was changed. No live publisher, bridge, or FastAPI process was started.

1. **Interoperability:** The inspected TinyIoT implementation supports type 28 FlexContainer only for registered SDT container definitions. It rejects an arbitrary CindySense definition and custom attributes outside a registered specialization. Its registered `org.onem2m.genericInterworkingService` was exercised historically in a disposable probe, but using it for workout metadata would misrepresent the schema. Per the task's stop condition, no CindySense FlexContainer was created or claimed. See [CSE capability findings](../cindysense-sdt-phase1.md) and [source/probe evidence](../onem2m-feature-audit.md). Required follow-up: register and validate a CindySense CND in the CSE, test it in a disposable CSE, then implement idempotent creation and a meaningful discovery consumer. Existing CNT/CIN data remains authoritative.
2. **MCP:** The existing two-server workflow now reports whether an already-completed workflow actually published, validates tool response shape before use, and tolerates optional MEC failures. The in-process demonstration runs real MCP protocol operations against mocked oneM2M and MEC data, writes to a temporary SQLite database, restarts the workflow, and confirms one publication. It does not publish to a live CSE or demonstrate live MEC authorization.
3. **Advanced oneM2M:** Existing Group fan-out, TimeSeries mirroring, discovery, subscription, and history implementations were inspected and covered by the regression tests below. Historical live results are referenced separately. Current local CSE and FastAPI were unavailable, so no new live claim is made.
4. **Security/privacy:** Existing ACP assignments and disposable permission probes were inspected. The already documented publisher ACP and subscription guard preserve separation for webcam writes and callback reads. Current production assignments could not be read back. No live ACP change or TLS deployment was attempted.

## File changes in this pass

| File | Purpose |
| --- | --- |
| [cindypt_mcp_agent.py](../../backend/cindypt_mcp_agent.py) | Handle malformed MCP replies and optional MEC failure; preserve accurate completed-publication state. |
| [test_cindypt_mcp.py](../../backend/tests/test_cindypt_mcp.py) | Add missing-summary, malformed-goal, and MEC 401 regression cases; reusable mocked two-server fixtures. |
| [mcp_workflow_demo.py](../../tools/mcp_workflow_demo.py) | Single-command, offline MCP and SQLite demonstration. |
| This report | Exact current evidence and demonstration status. |

Other modified and untracked files shown by `git status --short` predated this pass. They are not attributed to this work.

## Test results

Commands run from repository root unless a directory is shown. Test suites used local mocks or fixtures, not production writes.

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m unittest discover -s backend/tests -q` | 294 tests, OK; 0 reported skips. |
| `.venv-webcam/Scripts/python.exe -m unittest discover -s tools/tests -q` | 167 tests, OK; 0 reported skips. The first attempt under `.venv` failed at import because that environment lacks NumPy/OpenCV; the webcam environment passed. |
| `.venv/Scripts/python.exe -m unittest backend.tests.test_cindypt_mcp -q` | 10 tests, OK. Included in backend total; no reported skips. |
| `.venv/Scripts/python.exe -m tools.mcp_workflow_demo` | Exit 0; two MCP servers, one publication, durable completed state, duplicate prevented. |
| `cd dashboard; node --test src/services/*.test.js` | 47 passed, 0 failed/skipped. |
| `cd dashboard; npm.cmd run build` | Passed. |
| `cd dashboard; npm.cmd run lint` | Passed. |
| `git diff --check` | Passed; existing line-ending notices only. |

`npm.cmd run test -- --run` was attempted, but the dashboard has no `test` script. `node --test` is its actual service-test command. The unit suites do not establish physical webcam repetition accuracy.

## Rubric comparison

| Criterion | Before | After | Evidence | Remaining gap |
| --- | --- | --- | --- | --- |
| 1. Innovative oneM2M | Expert candidate | Proficient evidence; current live state unverified | Historical Group `/fopt` returned three CNT members; TS/TSI mirror and idempotence have offline tests. | Repeat read-only live Group and TS checks. |
| 3. Innovative scenario | Expert candidate | Proficient evidence; Expert claim deferred | Distinct Webcam, CindySense, CindyPT, Dashboard, and optional MEC bridge roles; offline subscription, summary, coaching, and dashboard tests. | Fresh end-to-end physical workout and live callback evidence. |
| 4. Security/privacy | Proficient | Proficient candidate based on historical disposable ACP tests | Distinct publisher ACP, subscription guard, allowed/denied test matrix and production migration evidence in [ACP plan](./acp-migration-plan.md). | Fresh assignment readback; HTTPS/TLS is unverified and local URL uses HTTP. |
| 5. Interoperability | Emerging | Emerging | Type/label discovery works in tests and historical probes; no legitimate CindySense FlexContainer. | Register custom CND in TinyIoT, then implement and test FCNT consumer. |
| 10. AI agents/MCP | Proficient | Stronger Proficient; Expert candidate only | Two actual in-process MCP servers, tool discovery, ordered calls, deterministic recommendation, SQLite recovery, duplicate prevention. | Live isolated CSE run and more fault-injection coverage; no autonomous reasoning claim. |

## Judge demonstration guide

Run the read-only commands only when the local CSE and FastAPI are healthy. The current check of `http://127.0.0.1:3001/TinyIoT` timed out and port 8000 did not connect. Do not use `--probe`, `setup`, or publication commands on production during a read-only rehearsal.

| Demonstration | Exact command or procedure | Expected observation | Status |
| --- | --- | --- | --- |
| FlexContainer discovery and retrieval | Inspect [CSE capability findings](../cindysense-sdt-phase1.md); after an approved CND registration, use CSE `GET ?fu=1&rcn=11&ty=28` and retrieve the discovered FCNT. | Current CSE has no registered CindySense CND; no CindySense FCNT result is expected. | **NOT VERIFIED** for CindySense; historical generic probe only. |
| MCP orchestration | `.venv/Scripts/python.exe -m tools.mcp_workflow_demo` | Both named MCP servers and tools, validated input, deterministic recommendation, SQLite `completed`, one publication after restart. | **MOCKED** external data; **LOCALLY TESTED** MCP and persistence. |
| Group membership/fan-out | `.venv/Scripts/python.exe tools/hackathon_preflight.py`; then `curl.exe -H "X-M2M-Origin: CDashboard" -H "X-M2M-RI: rubric-group" -H "X-M2M-RVI: 4" "http://127.0.0.1:3001/TinyIoT/CindySense_AE/WorkoutMonitoring_GRP/fopt"` | `m2m:agr` with three `m2m:rsp`, each RSC 2000 and CNT payload. | **NOT VERIFIED** today; historical **LIVE VERIFIED**. |
| TimeSeries retrieval | `curl.exe -H "X-M2M-Origin: CDashboard" -H "X-M2M-RI: rubric-ts" -H "X-M2M-RVI: 4" "http://127.0.0.1:3001/TinyIoT/CindySense_AE/WorkoutPerformance_TS?rcn=6&ty=30"` | TS child references to stored TSI, with no new TSI creation. TS has historical `mni=100`, `mbs=262144`, `cni=1`. | **NOT VERIFIED** today; historical **LIVE VERIFIED**. |
| Summary, recommendation, dashboard | `curl.exe http://127.0.0.1:8000/api/workout/history`; `curl.exe http://127.0.0.1:8000/api/cindypt/recommendations/current` | History and matching recommendation can be compared by workout ID; dashboard service tests verify consumption. | **NOT VERIFIED** live today; **LOCALLY TESTED** API/dashboard. |
| ACP allowed/denied | `.venv/Scripts/python.exe tools/onem2m_security_audit.py` | Read-only ACP definitions, resource `acpi`, and per-origin GET statuses; no callback URI or credential printed. | **NOT VERIFIED** today; historical disposable **LIVE VERIFIED**. |

The historical data flow is Webcam_AE pose/event writer → CindySense_AE repetition and summary CNTs plus optional TS → CindyPT_AE goal and recommendation CNTs → Dashboard_AE reader. The optional MECBridge_AE is a separate WLAN publisher when explicitly configured. oneM2M discovery, ACP, subscriptions, Group fan-out, and TimeSeries are service functions in this path; the present offline tests mock callbacks and CSE replies.

## Security and privacy limits

The webcam bridge publishes pose measurements, repetition events, and completed summaries as structured data, not raw frame bytes, to the CSE. The local webcam dashboard writes a current `frame.jpg` for its local preview and the workout runner writes local CSV metrics; neither should be described as zero image storage. SQLite journals retain pending/delivered repetition and summary state, and the MCP workflow retains identities, state, and diagnostic detail. No retention deletion guarantee or anonymization is implemented here. The documented publisher ACP permits CWebcam create/retrieve on two workout CNTs, CCindyPT and CDashboard retrieve, and CAdmin administration; the child subscription guard blocks CWebcam from reading callback configuration. TinyIoT's create privilege is broader than CIN-only creation. CindyPT and Dashboard AE policies still grant their owners broad `acop=63`, and MECBridge production ACP assignment is unverified. HTTP is configured by default; encryption in transit is not established.

## Remaining steps and recommendation

This is ready for an **offline MCP demonstration and code/test review**, but not a complete live four-criterion judge demonstration. The smallest remaining steps are: restore CSE/FastAPI availability under operator control; run the read-only commands above and confirm the history endpoint serves current code (a controlled FastAPI restart may be needed); validate a fresh physical workout separately; and, for criterion 5, register a reviewed CindySense FlexContainer schema in an isolated TinyIoT instance before implementing a consumer. HTTPS and production ACP changes require separate infrastructure review and explicit authorization. No automatic restart, live write, or ACP migration was performed.
