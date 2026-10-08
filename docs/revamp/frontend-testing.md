# Glass frontend quality baseline

## Scope and prerequisites

These checks do not change application behavior. Unit/component tests import the
production state module and mount the actual `RepeaterPoliciesView.vue`; they do
not reproduce its conversion or loading algorithms. API exports are mocked and
unmocked fetches fail locally. No RF hardware, broker, database, deployment,
credentials, or running backend is needed for unit tests, lint, typecheck, or build.

Use Node 22.12+ (or a supported newer even-numbered release). The chosen exact
Vitest 4.1.11 pin includes the security fix missing from 4.1.0 and supports Vite 8;
vue-tsc 3.2.6 accepts TypeScript >=5;
typescript-eslint 8.71.1 accepts TypeScript <6.1, including the existing TS 6.
jsdom 28 requires Node 20.19+, 22.12+, or 24+. Tool pins were checked against npm
metadata and exercised together after the parent integrator refreshed the lockfile
and completed `npm ci` with Vitest 4.1.11. No installation was needed for this repair.

For a fresh checkout, install the committed dependency set and run the gates:

```sh
cd /home/yellowcooln/openhop-dev/worktrees/glass-revamp/frontend
npm ci
npm run lint
npm run test:unit
npm run typecheck
npm run build
```

The refreshed lockfile is synchronized with package.json; dependency changes must
update both before CI uses `npm ci`.
`lint` is a deliberately limited TS/JS/Vue syntax, Vue parsing, and no-debugger
check, not a comprehensive style/security lint gate. `typecheck` runs the existing
strict tsconfig through vue-tsc, including Vue templates and tests, with no emit;
`build` remains Vite's separate production build. Do not suppress inherited type
errors or replace typecheck with a transpilation-only build.

## Regression contract and red demonstration

`npm run test:unit` includes six correctness controls and four explicitly named
`KNOWN DEFECT CHARACTERIZATION` cases. All ten use ordinary `it` assertions;
there is no expected-failure wrapper or exception-catching helper:

- Literal array condition values become the exact JSON string `["0x12","0x34"]`
  on visual roundtrip; the rest of the document is asserted unchanged.
- Advanced top-level/rule extension fields are dropped on visual roundtrip;
  the entire resulting scalar policy is asserted.
- An audit endpoint rejection discards a successful repeater list response,
  leaving exactly `[]`; error, sync and loading assertions remain normal.
- A slow command endpoint withholds already-loaded repeaters, leaving exactly
  `[]` while loading; request and loading assertions remain normal.

The controls cover scalar types, order/group references/objects, supported
`policy_engine` envelope unwrapping, exact advanced-document JSON-mode save,
sessionless loading, successful loading, role-gated user loading, and error/sync
reporting. These four characterizations are **not correctness passes**: they
assert exact known-incorrect outputs. A fix or any other output change fails the
baseline, prompting promotion to a correctness assertion. Setup, mounting,
input validation, parsing, API-call and cleanup errors also fail normally and
cannot be mistaken for a demonstrated defect.

Diagnostic red mode changes **only each targeted output expectation** to the
correct result; it never changes how the test body or unrelated assertions run:

```sh
npm run test:unit:red
# Or target one production regression:
GLASS_REGRESSION_RED=1 npm run test:unit -- src/tests/policy-roundtrip.test.ts
GLASS_REGRESSION_RED=1 npm run test:unit -- src/tests/partial-loading.test.ts
```

The red commands are diagnostic demonstrations, not PR success gates. Ensure the
working controls pass before interpreting a failure as evidence of the intended
defect (setup/import errors are not regression demonstrations).

`src/tests/fixtures/policies.ts` contains synthetic sanitized fixtures derived
from the editor examples and `types.ts` response shapes. Extension fields use the
actual opaque `Record<string, unknown>` contract, not a claim that every advanced
field is supported by the repeater runtime. Backend/Repeater legacy, null-radio,
multi-radio, missing-unit/unavailable sensor, unsupported-command and old/new
result fixtures belong to the separate fixture workstream; these frontend tests
do not claim that coverage.

## Explicit isolated browser target

Playwright does not start services, has no default URL, and refuses to load its
config unless both an explicit URL and isolation acknowledgement are supplied.
The operator must verify the URL is isolated; the acknowledgement cannot prove
that a host is safe. The smoke test blocks cross-origin browser requests and
non-GET/HEAD requests, uses an empty browser session, and only checks public
navigation and authentication redirect. It does not log in or queue commands.
Browser artifacts/tracing are disabled to avoid accidental sensitive captures.

Prepare a separate test frontend/backend stack with:

- A dedicated disposable PostgreSQL database and credentials, never a production
  database, snapshot, shared volume, or live backup.
- A dedicated Mosquitto instance/namespace and test credentials; no bridges,
  subscriptions or repeater command publishing to a live broker/RF network.
- A Glass backend configured exclusively against those isolated services.
- A frontend using only that backend (e.g. explicitly set `VITE_API_PROXY_TARGET`
  when running the existing dev server); do not assume its localhost:8080 default
  is an isolated backend. The browser's same-origin guard cannot validate the
  frontend server's upstream proxy target.
- Synthetic data only. Bootstrap may be incomplete for this unauthenticated
  smoke; do not initialize any real administrator account for it.

After the operator creates that stack and installs the Chromium binary separately:

```sh
npx playwright install chromium
GLASS_E2E_BASE_URL=http://127.0.0.1:15173 GLASS_E2E_ISOLATED=1 npm run test:e2e
```

The example URL is not a built-in fallback. Do not use live hosts. Browser tests
are not part of the default PR gate; PR checks should run lint, unit, typecheck,
and build, with no RF or deployment operations.

## Verification after integrator dependency installation

With the parent integrator's refreshed lockfile and completed `npm ci`, the
installed runner is Vitest 4.1.11. The earlier run passed six controls with four
expected failures; this repair replaces that masking mechanism. Reverification
with 4.1.11: `npm run test:unit` passed all ten normal tests (six correctness
controls and four known-defect characterizations). `npm run test:unit:red` exited
1 with six controls passing and exactly four targeted output assertions failing:
arrays became strings, extension fields disappeared, and both partial-loading
cases received an empty repeater list. No setup/import/cleanup failures occurred.
The setup binds storage from Vitest's exposed JSDOM instance before production
imports; Vitest's global `window` alias alone can retain Node's experimental
storage rather than the real JSDOM window storage.

Strict typecheck, lint and production build passed again in this repair using
existing installed tools. The build reported the existing large-chunk warning.
The four strict type errors were previously fixed without refactoring policy
conversion or loading behavior. No installation, services or browser run was
performed by this repair.

The parent integrator reports six inherited npm audit findings remain unresolved:
one moderate and five high. Updating Vitest removes its known advisory; this is
**not** a clean dependency/security audit and does not resolve the other findings.
