# Team workflow

This repository is arranged so the three work areas can be developed independently:

- **Firmware:** work in [firmware](../firmware/) and use a private `config.h`. Agree on sensor or event payloads with the backend teammate before changing what the device posts.
- **Backend and oneM2M:** work in [backend](../backend/), keep local `.env` out of Git, and use mocked clients in tests. Do not run [cindypt.py](../backend/cindypt.py) against a shared TinyIoT instance without coordinating; it posts a content instance.
- **Dashboard:** work in [dashboard](../dashboard/). Its demo workout state works without hardware. The API client can point to a teammate's local FastAPI instance through `VITE_API_BASE_URL`; browser variables are public.

Before integrating a change, review [API](api.md) together for route and payload changes. Use small topic branches and pull requests after GitHub setup, request another teammate's review, and record the local checks run. Avoid committing generated output, local data, firmware settings, or environment files.

## Working without the shared TinyIoT server

The dashboard's timer, exercise counters, history examples, and plan examples are local demo data. Backend tests mock TinyIoT requests, so backend logic can be developed without changing live resources. For connected UI work, run FastAPI against an isolated mock oneM2M response or agree on fixture JSON matching [API](api.md); do not treat the demo counters as live sensor measurements. Once the integration is ready, configure a reachable TinyIoT address and authorized read origins locally, then verify health and the specific read resources.

Keep the existing TinyIoT server, its ACPs, subscriptions, and database under the designated integration owner's control. Repository setup and routine teammate work should not mutate them.
