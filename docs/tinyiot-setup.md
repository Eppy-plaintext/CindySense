# TinyIoT external dependency

TinyIoT is maintained at [seslabSJU/tinyIoT](https://github.com/seslabSJU/tinyIoT). The locally inspected WSL Ubuntu checkout was clean on `main` at commit `c140495`. Keep that checkout and its database, build files, and server configuration outside this repository. Follow its upstream README for installation and database choices; the inspected setup uses port 3000.

The backend needs `TINYIOT_BASE_URL` reachable **from the backend process**, plus local read originators configured in ignored `backend/.env`. The dashboard never calls TinyIoT directly. Start and maintain TinyIoT separately; this repository does not provision its resources or change its access policies.

## Observed resource paths

The existing dashboard README recorded successful read access on 2026-10-27 for:

```text
TinyIoT/
├── CindySense_AE/
│   └── RepetitionCount/
│       └── la
└── CindyPT_AE/
    └── AIRecommendations/
        └── la
```

`la` means the latest content instance. These historical observations were **not rechecked against the live server while preparing this repository**. Confirm paths and permissions read-only in your own environment before connecting a component.

The firmware's configured target is `CindySense_AE/SensorData`. The separate Cindy PT script reads `CindySense_AE/WorkoutSummary/la` and writes to `CindyPT_AE/AIRecommendations`. Their current existence and access rights were not verified in this staging pass. Do not create or change containers, ACPs, subscriptions, or database records as part of repository setup.

Earlier local notes used an administrative originator for some reads. Replace that practice with independently authorized, least-privilege identities for the backend, Cindy PT, and firmware before sharing access with teammates. Keep all local originators and tokens out of Git and Vite environment variables.
