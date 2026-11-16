# CindySense SDT Phase 1

This phase defines `org.cindysense.device.cindySenseWorkoutTracker` as a custom
application information model. It is not a DeviceClass standardized by TS-0023.
The JSON returned by `GET /api/sdt/device` follows the nested JSON form described
in Annex C.3: the DeviceClass contains reusable ModuleClass objects and their
values.

## Model and mapping

`exerciseTracking`, `workoutSession`, `paceMonitoring`, and `coaching` are
reusable ModuleClasses. Observed/calculated workout values are read-only
DataPoints; identifiers, configured duration, target rounds, and detection source
are read-only Properties. Repetition, session-completed, and pace-warning
notifications are Events in the definition, but are not exposed as SDT Actions.
Phase 1 has no Actions because this endpoint is read-only.

The mapping uses retained validated `RepetitionEvent` objects from
`RepetitionCount`, existing workout snapshot totals, and existing CindyPT goal
and recommendation content. Unknown values are `null`, never zero. Event order
and session identity come from the validated event model.

## Specification basis

- TS-0023 5.2: lowerCamelCase naming, reusable ModuleClasses, the distinction
  between functional DataPoints and non-functional Properties, semantic R/W
  intent, and custom model guidance.
- TS-0023 6.2: the normative native mapping would use specialized
  `<flexContainer>` resources and `containerDefinition` values for DeviceClass
  and ModuleClass instances. This project does not perform that mapping in Phase 1.
- TS-0023 Annex C.3: the read-only JSON instance shape used by the endpoint.

## TinyIoT capability matrix

| Capability | Finding | Evidence / consequence |
|---|---|---|
| Basic `<flexContainer>` type 28 lifecycle | Supported | Read-only audit found POST/GET/PUT/DELETE and `ty=28` discovery for the registered generic specialization. |
| `containerDefinition` | Supported only for registered definitions | TinyIoT validates FCNT creation against its SDT registry. |
| Arbitrary CindySense specialization | Unsupported currently | No CindySense schema is registered; TinyIoT source rejects unregistered definitions. |
| Custom FCNT attributes | Supported inside a registered specialization | The disposable generic probe preserved its registered custom attribute; this does not authorize a CindySense FCNT. |
| Existing CNT/CIN mapping | Supported | Current `RepetitionCount` and `WorkoutSummary` remain ordinary containers/content instances and are read without migration. |
| Scoped resource discovery | Supported for observed types | Existing client and audit use `fu=1`, `rcn=11`, `ty`; production tree is unchanged. |
| Native TS-0023 CindySense tree | Unverified/not implemented | Requires a reviewed CSE-side schema and a separate migration; intentionally deferred. |

The capability evidence is recorded in `docs/onem2m-feature-audit.md`. No live
resource, access-control policy, subscription, or history was created, deleted,
or modified for this feature.

## Verification

```powershell
\.venv\Scripts\python.exe -m unittest backend.tests.test_sdt backend.tests.test_api
cd dashboard
npm.cmd run lint
npm.cmd run build
```

The dashboard reads the endpoint every 10 seconds only while the dashboard page
is active; it is informational and does not participate in the SSE repetition
path.
