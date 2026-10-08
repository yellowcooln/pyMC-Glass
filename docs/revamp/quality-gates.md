# Glass revamp quality gates (Task 2)

## Default PR checks

`.github/workflows/pr-checks.yml` runs on every pull request, pushes to `main`
and `dev`, and explicit manual dispatch. It uses two independent Ubuntu jobs,
15-minute job limits, read-only repository permissions, checkout without persisted
credentials, and cancellation of superseded runs. No deployment, RF operations,
real credentials, or application server startup is included.

| Job | Runtime / provisioning | Gates |
| --- | --- | --- |
| Backend lint and fixture tests | Python 3.12 (matching `backend/Dockerfile`); `python -m pip install -e 'backend[dev]'` | From `backend/`: `python -m ruff check app tests`, `python -m pytest tests` |
| Frontend lint, unit, typecheck and build | Node 22; `npm ci` using `frontend/package-lock.json` | From `frontend/`: `npm run lint`, `npm run test:unit`, `npm run typecheck`, `npm run build` |

Failures are not ignored or converted to green results. Strict `vue-tsc` checks
remain separate from Vite's transpilation/bundle build. Frontend lint currently
checks parsing and selected rules; it is not a comprehensive security/style audit.
GitHub branch protection must be configured separately to require these checks;
adding a workflow does not enforce merge protection by itself. No hosted run was
triggered or verified during this implementation.

The backend manifest declares Python >=3.11 and unpinned minimum dependency
versions, with no dependency lockfile. CI provisions fresh Python 3.12 dependencies;
local success on Python 3.14 does not certify that clean environment. The frontend
lockfile must remain synchronized with package.json, or `npm ci` fails intentionally.

## Isolation and what the backend tests actually exercise

`backend/tests/conftest.py` supplies a temporary SQLite file and PKI directory,
disables MQTT ingest, policy monitoring and administrator seeding, then enters
FastAPI's lifespan through `TestClient`. Lifespan still applies migrations and
generates temporary certificates. These are isolated API/fixture tests, not purely
startup-free unit tests and not PostgreSQL/Mosquitto integration tests.

CI additionally disables the alert action dispatcher and sets fallback SQLite,
runner-temporary PKI and test environment values before imports. Per-test fixtures
override the database/PKI with their own temporary paths and clear settings/database
caches. The CI job does not run Uvicorn, Compose, `make easy-start`, or seed a live
administrator. Existing Make lint/test targets depend on installation targets;
use direct commands when validating an already provisioned environment without
installations.

`test_mqtt_ingest.py` invokes the ingest processor against fixture data; its name
does not imply a broker connection. Likewise synthetic contract compatibility
fixtures are not proof that a real Repeater accepted a command or transmitted data.

## Executed local baseline

Working tree: `/home/yellowcooln/openhop-dev/worktrees/glass-revamp`, preserved
head `55c3ca2bc487022a6030ba0c1ed5819fec7d6f1f` plus in-progress Task 2 edits
(including the inform lint fix, contract fixtures and frontend testing work).
These results describe that dirty working tree, not the unchanged historical SHA.
Existing dependencies were used; no installation was performed by this CI task.

| Check | Actual result |
| --- | --- |
| Backend interpreter | Python 3.14.7 |
| `backend/.venv/bin/python -m ruff check backend/app backend/tests` | Passed: `All checks passed!` |
| `backend/.venv/bin/python -m pytest backend/tests` | 68 passed, 3 warnings, 134.12 seconds |
| Same pytest command with CI isolation environment, including `ALERT_ACTION_DISPATCHER_ENABLED=false` | 68 passed, 3 warnings, 137.67 seconds |
| Frontend runtime | Node v26.7.0, npm 11.19.0; not CI's Node 22 |
| `npm --prefix frontend run lint` | Passed |
| `npm --prefix frontend run typecheck` | Passed |
| `npm --prefix frontend run test:unit` | Vitest 4.1.11 after parent `npm ci`: 2 files passed, 10 ordinary tests passed (6 correctness controls, 4 known-defect characterizations) |
| `npm --prefix frontend run test:unit:red` | Exit 1: exactly 4 targeted output assertions failed, 6 controls passed; no setup/import/cleanup failures |
| `npm --prefix frontend run build` | Vite 8.1.0: passed, 100 modules; existing >500 kB chunk warning |
| Workflow syntax | Parsed with existing backend-venv PyYAML; asserted triggers, two jobs, read-only permission, no services, and frontend command sequence |
| `git diff --check` | Passed |

The three backend warnings concern deprecated Starlette/httpx TestClient use and
`HTTP_422_UNPROCESSABLE_ENTITY` aliases. The frontend's four known-defect
characterizations assert precise currently incorrect outputs with ordinary tests;
a green baseline does **not** mean those defects are fixed. Red mode changes only
the targeted output expectations, so unrelated exceptions and assertions cannot
be accepted as expected failures. A defect fix fails its characterization until
promoted to a correctness test. See [frontend-testing.md](frontend-testing.md).

The parent integrator refreshed the lockfile, completed `npm ci` with Vitest
4.1.11, and verified lint, strict typecheck, unit and build gates. This repair
reran all four gates against those existing installed tools with the results
above; the previously reported six passes/four expected failures are superseded
by ten ordinary passes. Six inherited npm audit findings remain unresolved
(one moderate, five high); the Vitest update is not a blanket clean audit.

The parent also installed the Repeater worktree virtualenv and reported 27 focused
Glass-handler tests passing. This repair added the missing standard-library /
third-party import separator in `tests/test_glass_handler.py`, then reran the
file's Ruff check (`All checks passed!`) and focused pytest (27 passed, 0.44s).
The Glass backend's 68-test results above remain the parent's verified baseline;
this frontend repair did not rerun that backend suite.

PyYAML parsing and structural assertions are not GitHub's full workflow validator.
`actionlint` was not available, so no actionlint pass is claimed. The initial YAML
write rejected an unquoted SQLite URL ending in a colon; the URL was quoted before
the successful syntax validation.

## Pending integration and browser gates

There are no actual PostgreSQL/Mosquitto integration suites in this baseline, so
the workflow deliberately contains **no idle service job or readiness-only gate**.
Neither SQLite success nor a healthy container is integration coverage.

When executable integration suites are added, pair them with disposable,
job-scoped PostgreSQL and Mosquitto environments and synthetic data/test-only PKI.
Assertions must exercise PostgreSQL migrations/queries and broker TLS/ingest or
command/result behavior, not just socket readiness. Give them separate check names,
bounded waits, failure diagnostics and unconditional cleanup; do not connect live
brokers, bridge RF networks, reuse production volumes or require production secrets.

The existing `npm run test:e2e` entry point is **not in the default PR workflow**.
Playwright requires both `GLASS_E2E_BASE_URL` and `GLASS_E2E_ISOLATED=1`; there is
no implicit URL or auto-started stack. An operator must verify the target and its
backend/database/broker are disposable and isolated before running it. See
[frontend-testing.md](frontend-testing.md) for exact setup and browser command.
No browser binary was installed, browser run executed, live service started,
PostgreSQL migration exercised, broker TLS handshake verified, RF command sent,
or deployment performed here. Integration/browser acceptance remains pending.
