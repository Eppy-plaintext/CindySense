# Group and discovery results

Existing group: `CindySense_AE/WorkoutMonitoring_GRP`, historically containing `RepetitionCount`, `WorkoutSummary`, and `WorkoutSessions` containers. Earlier readback found resource IDs in `mid`; historical `/fopt` GET returned all three CNT representations. The backend now checks the aggregate response count and each member RSC and reports `success` or `partial_failure`. The dashboard displays the group path, members, and member RSCs.

Discovery uses TinyIoT `fu=1&rcn=11&ty=3`, with optional `lbl`; results are explicitly marked CSE discovery and show the owning AE. This is an actual CSE request, not backend filtering. Earlier disposable type and label filters were verified in [onem2m-feature-audit.md](../onem2m-feature-audit.md). Both live endpoints are **NOT VERIFIED** in this session because TinyIoT was unreachable.

After restoring service, GET `/api/onem2m/group/workout-monitoring` and `/api/onem2m/discovery`; inspect `source`, group `members`, `member_statuses`, `overall_status`, and discovered references. Do not create a duplicate group or mutate its member content.

## GRP creation failure and corrected disposable test (2026-11-12)

The reported failed probe posted a type-9 GRP under the disposable **CNT** and received **HTTP 403 / X-M2M-RSC 4108**. TinyIoT defines 4108 as `RSC_INVALID_CHILD_RESOURCETYPE`. In `source/server/onem2m.c`, create checks `isValidChildType(parent, child)` before calling group validation. `source/server/util.c` permits a GRP child under an AE or CSEBase, but not under a CNT. Thus the failure was caused by the **GRP parent path**, not by `mt=3`, `mnm=2`, `csy=2`, member URIs, or ACP enforcement. No TinyIoT source patch was needed.

The group validator in `source/server/resources/grp.c` accepts `csy` values 0–3; value 2 is `CSY_ABANDON_GROUP`. It is appropriate here because both required CNT members must validate or creation should fail. For local structured member references, the validated format is `TinyIoT/CindySense_AE/<temporary-CNT>/labeled` and `TinyIoT/CindySense_AE/<temporary-CNT>/plain` (no leading slash). TinyIoT resolves these and stores each member's resource ID in `mid`.

The corrected probe created `CindySense_AE/AdvancedProbe_d04942d999f4` (disposable CNT), its two child CNTs, and the uniquely named sibling `CindySense_AE/AdvancedProbe_d04942d999f4_GRP` (GRP under the AE). The live result was:

| Operation | HTTP | X-M2M-RSC | Verified representation/result |
| --- | ---: | ---: | --- |
| Both member CNT GETs | 200 each | 2000 each | Existing disposable CNT representations |
| GRP POST under AE | 201 | 2001 | Creation succeeded |
| GRP GET | 200 | 2000 | `mt=3`, `mnm=2`, `csy=2`; stored `mid` exactly `3-20261112T093929,8780005` and `3-20261112T093929,9240006`, matching the two CNT `ri` values |
| GRP `/fopt` GET | 200 | 2000 | Two individual responses, each RSC 2000 with `m2m:cnt` representation |
| GRP DELETE / absent GET | 200 / 404 | 2002 / 4004 | Explicit sibling cleanup confirmed |
| Temporary CNT subtree DELETE / absent GET | 200 / 404 | 2002 / 4004 | Subtree cleanup confirmed |

All other probe checks passed in this live run; there were zero failed records. The corrected [probe](../../tools/onem2m_advanced_probe.py) records a failed GRP POST as an individual `passed: false` result, skips its readback/fan-out, and continues the remaining capability checks and cleanup. This live test touched only uniquely named disposable resources. It did not modify the existing WorkoutMonitoring group, workout content, or production ACPs. The full run output is local scratch evidence at `tmp/group_probe_20261112.json` and is not committed.
