# Resource tree snapshot

**Current availability:** TinyIoT stalled again after the recovery snapshot and was gracefully stopped. The snapshot below is preserved read-only evidence from the brief healthy interval; it is not a claim that the CSE is responding now. See [tinyiot-http-reliability.md](./tinyiot-http-reliability.md).

**Current live recovery snapshot (2026-11-12):** After a graceful one-process TinyIoT restart, Windows and WSL root GETs returned HTTP **200 / X-M2M-RSC 2000**. Read-only GETs of the publisher ACP, RepetitionCount, WorkoutSummary, RepCount_Sub, and Summary_Sub each returned **200 / 2000**. Their RIs matched the saved backup/migration evidence. RepetitionCount retained `cni=292`, `cbs=65513`; WorkoutSummary retained `cni=4`, `cbs=529`. The publisher ACP RI is `1-20261112T101707,2420075`; both workout containers reference it. Both subscriptions reference the existing CindySense_ACP RI `1-20261026T160317,2210029`, retain their original RIs, and retain their configured callback destinations and criteria. See [historical-coaching-replay-20261112.md](./historical-coaching-replay-20261112.md).

**Earlier failed snapshot:** The initial TinyIoT GET on port 3001 reset or timed out before returning HTTP or X-M2M-RSC. The successful snapshot above was collected only after recovery.

**Historical tree:** [onem2m-feature-audit.md](../onem2m-feature-audit.md) records `Webcam_AE`, `Dashboard_AE`, `CindyPT_AE`, `CindySense_AE`, three ACPs, `WorkoutMonitoring_GRP`, `WorkoutSummary`, `RepetitionCount`, `WorkoutSessions`, `FitnessGoals`, `AIRecommendations`, `RepCount_Sub`, and `Summary_Sub`. The current deployment must be read back before the demo.

Read-only commands after restoring TinyIoT (set the server URL and private originator in a local shell, not in Git):

```powershell
curl.exe -i -H "X-M2M-Origin: $env:TINYIOT_AUDIT_ORIGIN" -H "X-M2M-RI: audit-root" -H "X-M2M-RVI: 4" "$env:TINYIOT_BASE_URL"
curl.exe -i -H "X-M2M-Origin: $env:TINYIOT_AUDIT_ORIGIN" -H "X-M2M-RI: audit-tree" -H "X-M2M-RVI: 4" "$env:TINYIOT_BASE_URL?rcn=4"
```

For each AE/CNT/ACP/GRP/SUB, record `ri`, `ty`, `pi`, `cni`, `ch`, `acpi`, `aei`, and `mid` where applicable. Redact callback URLs and all private credentials before sharing output. A successful HTTP code alone is insufficient: inspect X-M2M-RSC and representation.
