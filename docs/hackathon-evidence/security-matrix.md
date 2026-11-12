# Live ACP security and permission matrix

## Phase 5 production migration (2026-11-12)

The user approved and the controlled migration succeeded. The original [pre-migration matrix](#production-policy-readback) below is preserved as historical evidence; the current assignments are recorded in [acp-migration-plan.md](./acp-migration-plan.md) and [acp-migration-execution-20261112.json](./acp-migration-execution-20261112.json). One new publisher ACP was created: RI `1-20261112T101707,2420075`, HTTP **201 / X-M2M-RSC 2001**. The two existing subscriptions were assigned the existing `CindySense_ACP` RI, then the two workout CNTs were assigned the new publisher RI; each PUT was **200 / 2004**, with GET readback **200 / 2000**. No existing ACP's `pv` or `pvs` was edited. Rollback was not needed.

| Current production GET | CWebcam | CCindyPT | CDashboard | CAdmin |
| --- | --- | --- | --- | --- |
| `RepetitionCount` | 200 / 2000 | 200 / 2000 | 200 / 2000 | 200 / 2000 |
| `WorkoutSummary` | 200 / 2000 | 200 / 2000 | 200 / 2000 | 200 / 2000 |
| `RepCount_Sub` | 403 / 4103 | 200 / 2000 | 200 / 2000 | 200 / 2000 |
| `Summary_Sub` | 403 / 4103 | 200 / 2000 | 200 / 2000 | 200 / 2000 |

POST verification used a disposable CNT assigned the **installed production publisher ACP**; no synthetic CIN was placed in the two historical workout CNTs. CWebcam GET **200 / 2000**, POST CIN **201 / 2001**, and CIN readback **200 / 2000** with matching content. CCindyPT and CDashboard GET **200 / 2000**, POST **403 / 4103**. Unrelated `CIntruder` GET and POST **403 / 4103**. Administrative cleanup DELETE **200 / 2002**; absent GET **404 / 4004**. Direct production workout CIN POST remains untested until a real webcam workout.

---

The following Phase 3 audit was performed before production migration, on 2026-11-12, against `http://127.0.0.1:3001/TinyIoT` using oneM2M RVI 4. These are **live TinyIoT results**, not mocked tests. No production ACP or workout resource was changed during that earlier audit. The read-only audit and disposable probes are implemented in [onem2m_security_audit.py](../../tools/onem2m_security_audit.py); the existing cross-AE write probe is [onem2m_acp_write_probe.py](../../tools/onem2m_acp_write_probe.py). All pairs below show **HTTP / X-M2M-RSC**.

## Production policy readback

All three ACP GETs by `CAdmin` returned **200 / 2000**. `acop` uses oneM2M permission bits: create 1, retrieve 2, update 4, delete 8, notify 16, discover 32.

| ACP | Resource ID | `pv.acr` | `pvs.acr` |
| --- | --- | --- | --- |
| CindySense_ACP | `1-20261026T160317,2210029` | CAdmin 63; CCindyPT 2; CDashboard 2 | CAdmin 63 |
| CindyPT_ACP | `1-20261026T160425,6490030` | CCindyPT 63; CAdmin 2; CDashboard 2 | CCindyPT 63 |
| Dashboard_ACP | `1-20261026T160433,5250031` | CDashboard 63; CAdmin 2 | CDashboard 63 |

`CindySense_AE`, `CindyPT_AE`, and `Dashboard_AE` reference their matching ACP resource IDs in `acpi`. `RepetitionCount`, `WorkoutSummary`, and `AIRecommendations` have no child `acpi` and inherit their parent AE policy. `Webcam_AE` has no explicit `acpi`; its owner is `CWebcam`. Administrative `CAdmin` access is a TinyIoT server bypass and is not evidence of least privilege.

## Read-only production GETs

| Resource | CWebcam | CCindyPT | CDashboard |
| --- | --- | --- | --- |
| `CindySense_AE` | 403 / 4103 | 200 / 2000 | 200 / 2000 |
| `CindySense_AE/RepetitionCount` | 403 / 4103 | 200 / 2000 | 200 / 2000 |
| `CindySense_AE/WorkoutSummary` | 403 / 4103 | 200 / 2000 | 200 / 2000 |
| `CindyPT_AE` | 403 / 4103 | 200 / 2000 | 200 / 2000 |
| `CindyPT_AE/AIRecommendations` | 403 / 4103 | 200 / 2000 | 200 / 2000 |
| `Dashboard_AE` | 403 / 4103 | 403 / 4103 | 200 / 2000 |
| `Webcam_AE` and `Webcam_AE/PoseData` | 200 / 2000 | 403 / 4103 | 403 / 4103 |

This verifies `CCindyPT` can retrieve the production summary container and `CDashboard` can retrieve workout and recommendation containers. Retrieval of a specific completed summary CIN by `CCindyPT` was not separately tested in this audit.

## Disposable policy tests

The workout test created one temporary ACP with `CWebcam` create+retrieve (`acop=3`) and `CCindyPT`/`CDashboard` retrieve (`acop=2`), attached by ACP resource ID to separate temporary Repetition and Summary containers. The coaching test used a separate temporary ACP with `CCindyPT` create+retrieve and `CDashboard` retrieve, attached to a temporary Recommendations container. Both ACP POSTs and all three CNT POSTs returned **201 / 2001**. Each CNT readback returned **200 / 2000** and contained the expected ACP resource ID in `acpi`.

| Temporary resource | Origin | GET container | POST CIN | GET created CIN and content |
| --- | --- | --- | --- | --- |
| Repetition | CWebcam | 200 / 2000 | 201 / 2001 | 200 / 2000; content matched |
| Repetition | CCindyPT | 200 / 2000 | 403 / 4103 | — |
| Repetition | CDashboard | 200 / 2000 | 403 / 4103 | — |
| Summary | CWebcam | 200 / 2000 | 201 / 2001 | 200 / 2000; content matched |
| Summary | CCindyPT | 200 / 2000 | 403 / 4103 | — |
| Summary | CDashboard | 200 / 2000 | 403 / 4103 | — |
| Recommendations | CWebcam | 403 / 4103 | 403 / 4103 | — |
| Recommendations | CCindyPT | 200 / 2000 | 201 / 2001 | 200 / 2000; content matched |
| Recommendations | CDashboard | 200 / 2000 | 403 / 4103 | — |

An additional disposable CNT inherited the **current production CindySense AE policy**: CWebcam GET **403 / 4103** and POST CIN **403 / 4103**; CCindyPT GET **200 / 2000**, POST **403 / 4103**; CDashboard GET **200 / 2000**, POST **403 / 4103**. Its creation returned **201 / 2001**. This confirms that ordinary `CWebcam` publishing into production workout containers currently lacks permission, without attempting a POST to those production containers.

The existing cross-AE write probe separately tested temporary children under the three application AEs. Owners `CWebcam` (Webcam_AE), `CCindyPT` (CindyPT_AE), and `CDashboard` (Dashboard_AE) each created a child CNT with **201 / 2001**, updated it with **200 / 2004**, and deleted a disposable child with **200 / 2002**. Every other ordinary application origin was denied each of those writes with **403 / 4103**. `CAdmin` succeeded through its administrative bypass. This verifies `CCindyPT` can write under the production CindyPT AE policy in a temporary subtree; POST directly to production `AIRecommendations` remains **NOT VERIFIED** to avoid changing workout data.

## Cleanup

The final scoped probe deleted all three test CNTs and both test ACPs with **200 / 2002** using `CAdmin` solely for disposable cleanup; GET of each returned **404 / 4004**. The inherited-policy CNT and each cross-AE probe root also deleted with **200 / 2002** and returned **404 / 4004** afterward.

During the first scoped run, `CCindyPT` DELETE of its temporary Recommendations CNT returned **403 / 4103**, as expected from `acop=3`. That first run deleted its ACP before the CNT. We repaired the cleanup procedure and removed the remaining CNT with `CAdmin` DELETE **200 / 2002**; follow-up GET was **404 / 4004**. The second run cleaned every temporary resource in order. No disposable resources are known to remain.

## Historical Phase 3 proposal — subsequently applied in Phase 5

1. Create one dedicated publisher ACP whose `pv` grants `CWebcam` create+retrieve (`acop=3`), preserves `CCindyPT` and `CDashboard` retrieve (`acop=2`), and retains `CAdmin` administrative access. Keep `pvs` restricted to `CAdmin`. Do not add `CWebcam` to `CindySense_ACP` at the AE level.
2. Before assigning the publisher ACP to the two workout CNTs, explicitly assign the **existing** `CindySense_ACP` to `RepCount_Sub` and `Summary_Sub`. A child subscription otherwise inherits the new parent policy and could expose its callback URI to `CWebcam`. The existing ACP guard was verified on a disposable SUB: CWebcam GET of the parent CNT **200 / 2000**, GET of the child SUB **403 / 4103**; CCindyPT and CDashboard GET of both **200 / 2000**.
3. Assign the new publisher ACP only to `RepetitionCount` and `WorkoutSummary`. The exact disposable apply and null rollback, including the subscription guard, were verified live. The complete before/after table, backups, order, and executed production result are in [acp-migration-plan.md](./acp-migration-plan.md).

`CindyPT_ACP` currently gives `CCindyPT` `acop=63` across its AE. It works, but exceeds recommendation-only write privilege. A later scoped ACP for `AIRecommendations` could narrow this after goal read, recommendation write, dashboard read, and subscription paths are checked. `Dashboard_ACP` grants `CDashboard` `acop=63` on its own AE; its necessity was not evaluated here. No production ACP was modified.

## Reproduction

```powershell
python tools/onem2m_security_audit.py
python tools/onem2m_security_audit.py --probe
python tools/onem2m_acp_write_probe.py
```

`--probe` creates uniquely named temporary ACP/CNT resources and removes them. It requires a working local TinyIoT CSE and the configured test origins. Automated tests mock the CSE and are separate evidence from the live results above.
