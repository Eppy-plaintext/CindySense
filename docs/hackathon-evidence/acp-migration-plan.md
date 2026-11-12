# Production webcam publisher ACP migration and rollback record

Prepared 2026-11-12 and subsequently approved by the user. The original proposal and rollback instructions remain below. The production execution results are recorded here and in [acp-migration-execution-20261112.json](./acp-migration-execution-20261112.json). The original backups were not overwritten.

## Approved production execution

Preflight found one WSL TinyIoT `server` process listening on port 3001 (PID 4958). Windows GET of the CSE returned **HTTP 200 / X-M2M-RSC 2000**. Backup SHA-256 checks, all backed-up resource IDs, ACP `pv`/`pvs`, AE/CNT/SUB assignments, subscription event criteria and URI counts, and existing authorized/denied GETs matched the original backups. The proposed new ACP path was absent **404 / 4004** before creation.

The migration **succeeded**. The new `CindySense_AE/CindySense_WebcamPublisher_ACP` has RI `1-20261112T101707,2420075`. Its creation returned **201 / 2001**; GET readback returned **200 / 2000** with the exact approved `pv` and `pvs`. The only four existing resources updated were, in order:

| Resource | Before `acpi` | After `acpi` | PUT | GET readback |
| --- | --- | --- | --- | --- |
| `RepetitionCount/RepCount_Sub` | inherited | `[1-20261026T160317,2210029]` | 200 / 2004 | 200 / 2000 |
| `WorkoutSummary/Summary_Sub` | inherited | `[1-20261026T160317,2210029]` | 200 / 2004 | 200 / 2000 |
| `RepetitionCount` | inherited | `[1-20261112T101707,2420075]` | 200 / 2004 | 200 / 2000 |
| `WorkoutSummary` | inherited | `[1-20261112T101707,2420075]` | 200 / 2004 | 200 / 2000 |

After each update the migration runner verified the stored assignment and relevant access paths. The two subscriptions kept their exact preflight `nu`, `enc`, `nct`, names, and RIs; callback values were compared in memory and not written to the execution log. **Rollback was not necessary.** The rollback sequence below remains available if a later review finds a problem.

Read-only content counts matched the original backups after migration: `RepetitionCount` `cni=292`, `cbs=65513`; `WorkoutSummary` `cni=4`, `cbs=529`. No historical workout CIN was deleted or updated.

Post-migration GET of both workout CNTs by CWebcam, CCindyPT, CDashboard, and CAdmin returned **200 / 2000**. CWebcam GET of each protected SUB returned **403 / 4103**, while CCindyPT, CDashboard, and CAdmin GET returned **200 / 2000**. A disposable CNT referencing the installed publisher ACP verified CWebcam POST CIN **201 / 2001** and CIN readback **200 / 2000**; CCindyPT and CDashboard POST were **403 / 4103**; unrelated `CIntruder` GET and POST were **403 / 4103**. Its cleanup returned DELETE **200 / 2002** and absent GET **404 / 4004**. No synthetic CIN was posted into a production workout CNT.

## Backups and preconditions

- Complete read-only ACP representations, `CindySense_AE` assignment, and both workout CNT representations: [acp-production-backup-20261112.json](./acp-production-backup-20261112.json), SHA-256 `2e64ee888166a4b0e5637c6fa9fdd2f0df3afd095efa1c418cb9cdd5c917423f`.
- Existing subscription assignments and event configuration, with callback URI values omitted: [acp-subscription-assignments-backup-20261112.json](./acp-subscription-assignments-backup-20261112.json), SHA-256 `2e134ae7cea6c0130f46a9a13bd13cb469a2df6d575ff5dc924065376b7c2ca5`.
- Before any approved migration, take fresh backups and compare resource IDs, `acpi`, and subscription names. Stop if they changed. Keep the current callback URI in TinyIoT; never export its token in evidence or logs.
- Backend configuration currently uses `TINYIOT_SUBSCRIPTION_ORIGIN=CAdmin`, `TINYIOT_CINDYPT_ORIGIN=CCindyPT`, and `TINYIOT_WORKOUT_ORIGIN=CDashboard`. These are local configuration values, not permissions to broaden the policy.

All three ACP readbacks returned **200 / 2000**. The current `CindySense_ACP` RI is `1-20261026T160317,2210029`; `pv` is CAdmin 63, CCindyPT 2, CDashboard 2, and `pvs` is CAdmin 63. `CindyPT_ACP` and `Dashboard_ACP` remain unchanged. Both workout CNTs and their existing `RepCount_Sub` and `Summary_Sub` have no explicit `acpi`. Both subscription GETs by CAdmin returned **200 / 2000**; both have `enc.net=[3]`, `nct=1`, and one callback URI each. Their URI values were deliberately not recorded.

## Exact before and proposed after

| Resource | Before | Proposed after | Purpose |
| --- | --- | --- | --- |
| `CindySense_ACP` | CAdmin 63; CCindyPT and CDashboard 2 | **Unchanged** | Existing parent policy and subscription guard |
| `CindySense_AE` | `acpi=[1-20261026T160317,2210029]` | **Unchanged** | Avoid widening unrelated children |
| New `CindySense_WebcamPublisher_ACP` | Absent | `pv`: CAdmin 63, CWebcam 3, CCindyPT 2, CDashboard 2; `pvs`: CAdmin 63 | Create+retrieve on workout CNTs only |
| `RepetitionCount/RepCount_Sub` | No explicit `acpi` | `acpi=[1-20261026T160317,2210029]` | Preserve its current effective policy; keep callback URI unreadable by CWebcam |
| `WorkoutSummary/Summary_Sub` | No explicit `acpi` | `acpi=[1-20261026T160317,2210029]` | Same guard for summary callback |
| `RepetitionCount` | No explicit `acpi`; inherits `CindySense_ACP` | `acpi=[new publisher ACP RI]` | Allow non-admin webcam CIN publishing |
| `WorkoutSummary` | No explicit `acpi`; inherits `CindySense_ACP` | `acpi=[new publisher ACP RI]` | Allow non-admin completed-summary publishing |

The new ACP representation to create under `CindySense_AE` is:

```json
{
  "m2m:acp": {
    "rn": "CindySense_WebcamPublisher_ACP",
    "pv": {"acr": [
      {"acor": ["CAdmin"], "acop": 63},
      {"acor": ["CWebcam"], "acop": 3},
      {"acor": ["CCindyPT", "CDashboard"], "acop": 2}
    ]},
    "pvs": {"acr": [{"acor": ["CAdmin"], "acop": 63}]}
  }
}
```

The exact future RI is assigned by TinyIoT at creation. Use that retrieved RI in the two CNT `acpi` assignments. ACP update requests must contain only `acpi`, for example `{"m2m:cnt":{"acpi":["<new-publisher-ACP-RI>"]}}` and `{"m2m:sub":{"acpi":["1-20261026T160317,2210029"]}}`. Use `CAdmin` solely for this administrative migration; ordinary webcam publishing uses `CWebcam`.

## Disposable verification completed

The exact publisher ACP was created in a disposable subtree (**201 / 2001**). Temporary Repetition and Summary CNTs stored its RI and allowed CWebcam GET **200 / 2000**, POST CIN **201 / 2001**, and CIN readback **200 / 2000** with matching content. CCindyPT and CDashboard could GET **200 / 2000** and were denied POST **403 / 4103**.

A separate disposable migration round trip started with inherited policy, where CWebcam GET was **403 / 4103**. `PUT acpi=[new RI]` returned **200 / 2004**, the stored RI matched, CWebcam GET returned **200 / 2000**, and POST CIN returned **201 / 2001**. `PUT acpi=null` returned **200 / 2004**, stored `acpi` became null, CWebcam GET reverted to **403 / 4103**, and CCindyPT/CDashboard GET remained **200 / 2000**. Its temporary CNT and ACP were each deleted **200 / 2002** and confirmed absent **404 / 4004**.

The exact minimal subscription guard was tested on a disposable SUB. The existing `CindySense_ACP` was assigned explicitly to the SUB first (**200 / 2004**), then the disposable publisher ACP to its parent CNT (**200 / 2004**). CWebcam GET CNT **200 / 2000**, POST CIN **201 / 2001**, but GET SUB **403 / 4103**. CAdmin, CCindyPT, and CDashboard GET of both CNT and SUB returned **200 / 2000**. Parent `acpi=null` and then SUB `acpi=null` each returned **200 / 2004**; the SUB's stored `acpi` became null, CWebcam GET of parent and SUB reverted to **403 / 4103**, and CCindyPT GET of SUB remained **200 / 2000**. The CNT, SUB, and temporary publisher ACP were confirmed absent after cleanup (**200 / 2002** DELETE of CNT/ACP, **404 / 4004** GET). No callback URL or token was used in this test.

## Approved apply sequence — executed as recorded above

1. Stop webcam publishing for the short migration window. Recheck the four existing target RIs and assignments against the fresh backup; verify both subscriptions still exist with `enc.net=[3]` and their callback URI values still present without printing those values.
2. Create `CindySense_WebcamPublisher_ACP` under `CindySense_AE` as type 1. Require HTTP **201** / RSC **2001**. GET it, verify exact `pv`, `pvs`, and RI. Stop and roll back on any difference.
3. Set `acpi=[existing CindySense_ACP RI]` on `RepCount_Sub` and `Summary_Sub` **before** changing their parents. Require PUT **200 / 2004** and GET **200 / 2000** with the exact stored reference. Verify CAdmin, CCindyPT, and CDashboard GET remain allowed, CWebcam GET remains **403 / 4103**. Stop if either subscription changes its `nu`, `enc`, or `nct`.
4. Set `acpi=[new publisher ACP RI]` on `RepetitionCount` and `WorkoutSummary`, each with a separate `m2m:cnt` PUT containing only `acpi`. Require **200 / 2004** and exact GET readback. Check CWebcam GET **200 / 2000**, CCindyPT/CDashboard GET **200 / 2000**, and CWebcam GET of both subscriptions **403 / 4103**.
5. Verify the existing subscriptions are still configured for event type 3 and that the backend can receive a real completed workout notification. Do not inject synthetic workout data into production. A real webcam workout may be tested only after the permission migration is approved and applied.

## Rollback of any partial migration

1. Stop webcam publishing. Use the fresh backup to confirm the original assignments were absent, not an empty array. For each changed workout CNT, PUT only `{"m2m:cnt":{"acpi":null}}` with CAdmin. Require **200 / 2004**, GET **200 / 2000**, and null/absent `acpi`; verify CWebcam GET is again **403 / 4103** and CCindyPT/CDashboard GET remains **200 / 2000**.
2. Only after both parent CNTs are restored, PUT `{"m2m:sub":{"acpi":null}}` on each changed SUB. Require **200 / 2004** and confirm null/absent `acpi`, original `enc`, `nct`, and URI count. This parent-then-SUB null rollback order was live verified on disposable resources.
3. Delete the new publisher ACP only after confirming no CNT references its RI. Require **200 / 2002** and absent GET **404 / 4004**. Recheck the parent AE and original ACP assignments against the backup.
4. If a step fails, leave the new ACP in place, retain the backup, and stop. Do not delete an ACP still referenced by a resource.

## Remaining risks and approval boundary

- TinyIoT ACP create permission on a CNT is not restricted to CIN type by this policy. CWebcam can potentially create another allowed child type, such as a new SUB, under the two workout CNTs. This is a CSE policy limitation of direct publishing; the guard prevents reading the **existing** subscription callback URI.
- FastAPI was started on port 8000 with access logs disabled to keep the callback token out of logs. The existing SUB notification URLs matched backend configuration exactly; no URL or event criteria was changed. A disposable SUB passed TinyIoT's callback verification request **201 / 2001** and was cleaned up. A real completed-workout notification and stored CindyPT recommendation remain **NOT VERIFIED** until a physical webcam session completes.
- The user explicitly approved the ACP and four assignment changes. The executed result and unchanged original backups are recorded above.
