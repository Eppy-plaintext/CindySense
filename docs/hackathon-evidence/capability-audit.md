# Capability audit — 2026-11-12

The current preflight found a Windows listener on port 3001, but GET of `http://127.0.0.1:3001/TinyIoT` reset the connection (curl HTTP 000). Port 3000 and FastAPI port 8000 were unavailable. WSL listed Ubuntu-22.04 as running. No current resource read, child count, ID, ACP, or mutation could be verified. No live resource was changed during this work.

The earlier live audit in [onem2m-feature-audit.md](../onem2m-feature-audit.md) records a different, working TinyIoT session. Its observations are historical evidence, not a successful 2026-11-12 test.

| Capability | Earlier live evidence | Current status |
| --- | --- | --- |
| GRP type 9 and fanOutPoint GET | `WorkoutMonitoring_GRP` readback and three CNT fan-out members | NOT VERIFIED today; historically SUPPORTED |
| CSE discovery by type | `fu=1&rcn=11&ty=3` returned CNT references | NOT VERIFIED today; historically SUPPORTED |
| CSE label discovery | Disposable matching and missing labels separated results | NOT VERIFIED today; historically SUPPORTED |
| ACP enforcement | Authorized application reads/writes and denied cross-AE operations on disposable CNTs | NOT VERIFIED today; historically PARTIALLY SUPPORTED for least privilege because CAdmin bypassed narrower ACP entries |
| SUB notification | Historical repetition and summary callbacks returned RSC 2000 after CIN writes | NOT VERIFIED today; historically SUPPORTED |
| TS/TSI | Disposable TS/TSI creation, readback, counters and cleanup | NOT VERIFIED today; historically PARTIALLY SUPPORTED for workout analytics |
| Automatic CindyPT persistence | New code and mock tests | NOT VERIFIED live |

The read-only [hackathon_preflight.py](../../tools/hackathon_preflight.py) checks CSE status, discovery, resource IDs, types, child counts, AE IDs, ACP references, and group members without printing secret content. The isolated [onem2m_advanced_probe.py](../../tools/onem2m_advanced_probe.py) now includes temporary GRP and fan-out readback alongside type/label discovery and TS/TSI checks. [onem2m_filter_probe.py](../../tools/onem2m_filter_probe.py), [onem2m_retention_acp_probe.py](../../tools/onem2m_retention_acp_probe.py), and [onem2m_acp_write_probe.py](../../tools/onem2m_acp_write_probe.py) cover disposable SUB and ACP checks. They use disposable resources with cleanup, but should run only after a healthy read-only preflight. A full current capability matrix cannot be claimed from their historical runs.

The backend continues to use oneM2M RVI 4 in requests; callback validation accepts RVI 3 or 4. No Release 4 only server behavior is assumed.
