# CindySense repository consolidation

This plan records the integration of the existing
`Eppy-plaintext/cindysense-dashboard` checkout into
`Eppy-plaintext/CindySense`. It is for review on the local
`integration/consolidate-dashboard` branch. Do not push or merge it until
the three teammates have reviewed the branch. Neither repository is deleted.

## Source inventory and mapping

| Standalone dashboard repository | CindySense destination | Decision |
| --- | --- | --- |
| `backend/` | `backend/` | All tracked code and tests already match byte-for-byte; retain CindySense's additional `cindypt.py`. |
| Root React/Vite files, including `.env.example` | `dashboard/` | All tracked files already match byte-for-byte. |
| Root `README.md` | `docs/dashboard-standalone-notes.md` | Preserve detailed prototype notes as historical reference; keep the team-oriented CindySense root README as the entry point. |
| Root `.gitignore` | Root `.gitignore` | Keep CindySense's more comprehensive rules for secrets, build output, local data, and firmware configuration. |
| `backend/.env.example` | `backend/.env.example` | Keep originators blank; carry forward warnings about the prototype identities and simulation-write token. |

CindySense already contains `firmware/` and `docs/`, which the standalone
dashboard repository does not. The integration branch starts from CindySense
`f3bbb13ea81c65bff3562d27a136e9084a770cf2`; the standalone dashboard
comparison uses `5e4ad6648213b73c43ce7d6d9763bdc506d84630`.
The separate Git histories remain available in their existing repositories.

## Review and safety checks

1. Review the branch diff, especially the archived notes and the wording in
   `backend/.env.example`. Verify no actual `.env`, `config.h`, token, key,
   or generated artifact is staged.
2. Run mocked backend tests, dashboard build, and dashboard lint. These checks
   do not require or modify a TinyIoT server or PostgreSQL database.
3. Have the firmware, backend, and dashboard owners review paths and API
   contracts. Resolve any differences found after these source commits before
   merging. The standalone dashboard repository remains intact and can be
   archived separately only after the team decides how to handle it.

This branch does not change runtime routes, oneM2M permissions or resources,
database state, or firmware behavior.
