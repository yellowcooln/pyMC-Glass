# Glass revamp: preserved baseline and source reuse classification

## Decision and scope

Retain the existing Glass feature branch and its history. Do **not** replace it with a fresh upstream-dev baseline, reset, rebase, squash, or import history from an unrelated active checkout. This report is a source/ancestry baseline, not a runtime acceptance report. Only this document is added to Glass; no application code is changed.

The parent performed the fresh fetch before this inspection. The identities below were independently read from local Git objects after that fetch; this subtask did not fetch or independently query live GitHub/PR metadata. “PR-only” below means present in the preserved feature tree relative to the fetched upstream `origin/dev`, not a claim about current GitHub PR state.

## Independently checked identities and ancestry

| Target | Branch/ref | Exact SHA |
| --- | --- | --- |
| Glass preserved working head | `feat/repeater-dev-policy-glass` | `55c3ca2bc487022a6030ba0c1ed5819fec7d6f1f` |
| Glass preserved fork-tracking ref | `refs/remotes/yellowcooln/feat/repeater-dev-policy-glass` | `55c3ca2bc487022a6030ba0c1ed5819fec7d6f1f` |
| Glass fetched upstream development | `origin/dev` | `bd114908eae35701dab8691bea88d69e0bd29f83` |
| Glass merge base | `merge-base origin/dev HEAD` | `bd114908eae35701dab8691bea88d69e0bd29f83` |
| Repeater revamp working head | `feat/glass-revamp` | `3c4bf3a9586d1e0b3871091649bc3fd09da3b662` |
| Repeater fetched upstream development | `origin/dev` | `3c4bf3a9586d1e0b3871091649bc3fd09da3b662` |
| Unchanged primary Glass checkout | `main` | `9cb3b542a0cffcae60cc95ce93a00dda99ad1bd6` |
| Unchanged primary Repeater checkout | `feat/sensor-manager` | `b51aed023d9f50722b4d84e6f954685c16e5d1cb` |

Read-only Git results:

- `git rev-list --left-right --count origin/dev...HEAD` in Glass: **0 upstream-only, 55 feature-only commits**.
- `git merge-base --is-ancestor origin/dev HEAD`: exit **0**. Upstream development is already an ancestor; **no reconciliation merge is needed at this baseline**. Any later normal merge belongs to the parent.
- Fork-tracking ref versus working head: **0 / 0** commits; `git diff --quiet yellowcooln/feat/repeater-dev-policy-glass HEAD` exited **0**. Original feature work is recoverable through the exact retained head/ref, not merely through descriptions of it.
- `git log --merges origin/dev..HEAD`: no feature-range merge commits. No new unrelated merge history was introduced by this task. This does not certify every historical change as desirable.
- `git diff --stat origin/dev...HEAD`: **125 files changed, 7392 insertions, 1016 deletions**. This is the pre-report feature delta, not the size of this documentation change.
- `VERSION`: fetched upstream **1.0.4**, preserved feature **1.1.0**. Backend manifest/app version and frontend manifest are also 1.1.0 in the inspected feature tree.

## Checkout, remote, and worktree preservation

Inspection used `/home/yellowcooln/openhop-dev/worktrees/glass-revamp` and read-only Git discovery in `/home/yellowcooln/openhop-dev/worktrees/repeater-glass-revamp`. Neither primary checkout was switched, reset, cleaned, or edited.

- Glass remote: `origin` fetch/push `https://github.com/openhop-dev/openHop-Glass.git`. `git remote -v` lists only origin; the existing `yellowcooln/...` remote-tracking ref is present even though no corresponding remote is configured. Do not delete that recovery reference.
- Repeater remotes: `origin` fetch/push `https://github.com/openhop-dev/openhop_repeater`; `yellowcooln` fetch/push `https://github.com/yellowcooln/openHop_Repeater.git`.
- Initial Glass revamp status: clean, branch `feat/repeater-dev-policy-glass`.
- Initial Repeater revamp status: clean, branch `feat/glass-revamp...origin/dev`.
- Primary Glass status: `main...origin/main`, pre-existing untracked `.hermes/`.
- Primary Repeater status: `feat/sensor-manager...origin/dev [behind 100]`, pre-existing untracked `.hermes/`. That active feature checkout is **not** the Repeater source baseline.
- Glass worktree inventory contains only primary Glass and Glass revamp. Full Repeater discovery is recorded in the namespaced task1 report, including the pre-existing prunable detached entry; none was removed or repaired.

## Source-based reuse classification

These classifications come from actual `git diff origin/dev...HEAD` and current source inspection, not PR prose or old audit conclusions. Existing shared infrastructure remains inherited from upstream; feature additions should be kept in place and evolved rather than reconstructed from descriptions.

| Area | Classification | Verified source and reusable behavior | Limits / follow-up |
| --- | --- | --- | --- |
| openHop product branding | PR-only implementation; reuse | `frontend/index.html`, `frontend/public/favicon.svg`, `frontend/src/assets/logo/openhop_*`, `views/AuthView.vue`, `components/layout/{SidebarNav,TopHeader}.vue`, backend `config.py`, `services/pki.py`, manifests. Login/navigation/header identify openHop Glass; network terminology replaces fleet copy. | Brand changes are not protocol/database renames. Browser storage keys in `state/appState.ts`, sidebar preferences and release cookie changed; no legacy-key fallback is evidenced by those diffs, so preserved server data does not imply preserved browser login/preferences. |
| Light/dark theme | PR-only implementation; reuse | `composables/useTheme.ts`, `components/ThemeToggle.vue`, `App.vue`, base styles and sidebar contrast variables. Stored theme preference and system preference initialize the document dark class. | Source presence only; no visual/accessibility/browser verification here. |
| Runtime repeater policy templates | PR-only implementation; reuse with contract review | Backend `api/routes/repeater_policy.py`, `schemas/repeater_policy.py`, `services/repeater_policy.py`, registered in `main.py`; `db/models.py` and migration `0013_repeater_policies.sql`; frontend `api.ts`, `types.ts`, router and `views/RepeaterPoliciesView.vue`. CRUD, validation, visual/JSON editor, rule IDs, ALL/ANY, allow/drop/log_only, channel-sender field, examples, Objects tab, channel-hash/pubkey groups and group references are source-backed. | Glass normalization is structural, not a full Repeater policy-engine validator: objects are accepted as a dictionary and operators/field semantics/group references are not comprehensively checked. Reuse is not a parity certification. |
| Runtime policy rollout/status | PR-only control implementation; reuse | Route `/api/repeater-policies/sync` queues `policy_sync` for selected IDs or all eligible repeaters excluding pending/rejected, with replace/patch and validate-only fields. Reads allow admin/operator/viewer; mutations admin/operator with audit records. `inform.py` marks dispatched/results through service helpers; UI shows latest per-repeater status. | Delivery uses the existing inform command queue, not a new direct-control/MQTT transport. Actual Repeater support and end-to-end completion remain pending. Latest row is per repeater, not an immutable rollout history; helpers can overwrite its command ID without checking it matches the current queued command, requiring later concurrency/stale-result review. |
| Sensor readings | PR-only telemetry implementation; reuse as ingest/display foundation | `contracts/v1/inform.py` accepts optional dict/list sensors; `api/routes/inform.py` stores top-level sensors under `system_json["sensors"]`. `views/RepeaterDetailView.vue` renders arrays or `{readings: [...]}`, status/error/timestamp, nested metrics and heuristic units. | Per-repeater latest summary, not sensor CRUD, configuration/control, fleet sensor management or a dedicated sensor time-series API. Arbitrary accepted sensor dictionaries are not necessarily rendered. `services/mqtt_ingest.py` delta inspected is formatting, **not** a new sensor MQTT ingestion path. |
| Data/schema migration | PR-only compatibility work plus inherited runner; reuse cautiously | `MIGRATION.md` separates branding from DB renaming; Compose retains `pymc_glass` connections, existing named-volume keys and `./pki` mount; new `postgres-init` creates missing legacy/new DB names and backend waits for completion. Migrations 0013 (policy tables/indexes) and 0014 (`repeaters.open_url`) extend existing schema. `db/migrate.py` retains sorted stem tracking and 0011 legacy-prefix alias. | No DB/volume/PKI migration was executed. Compose currently has **no top-level project name** or explicit volume `name`; changed install directory/project identity can select different physical volumes. A historical “Set openHop compose project name” commit is not proof of current behavior. Creating an empty new DB is not migrating data. The runner still splits SQL on semicolons; old applied migrations must remain immutable. |
| Proxmox installer/update | PR-only script implementation; preserve for separate hardening | `scripts/proxmox-install.sh`: interactive host/root preflight, Debian 12 LXC, 4096 MB/4-core defaults, explicit start handling, production Compose reuse, backend env materialization, health diagnostics, update helper/aliases and manual-ready TUN setup. Tailscale auto-install is absent at the retained head. | Not authorized to run. Still defaults to legacy repo URLs and main, uses privileged/unconfined LXC, root autologin, static bootstrap credentials, destructive app-directory replacement inside the new CT and restart-oriented updater. Nested generated-script interpolation must be audited, not assumed fixed from historical commit titles. Resource prompts are not all numerically validated in the current source. |
| Production env bootstrap | PR-only operational change; retain but review | `Makefile init-prod-env` now copies backend production env to `backend/.env`; installer also materializes that file. | This can overwrite an existing backend env; do not mistake this Make target for a read-only check or a universally non-overwriting initializer. |
| Managed MQTT certificate hosts | PR-only implementation; reuse | `schemas/system_settings.py`, `api/routes/system_settings.py`, `services/system_settings.py`, `main.py`, `views/SettingsView.vue`: additional SAN hosts are sanitized/deduplicated and passed to broker certificate generation, separately from fields sent as active Repeater broker settings. | Existing PKI/MQTT architecture is inherited. No certificates were generated or live TLS checked. |
| Repeater open-UI targets | PR-only implementation; reuse | `utils/repeaterUi.ts`, inventory/detail views, backend repeater schema/routes/model and migration 0014: configurable browser URL independent of inform/control IP; default HTTP port from settings or 8000; loopback/bridge gateway fallback. `inform.py` avoids replacing a known IP with a Docker bridge source. | Browser navigation convenience, not authenticated proxy or control transport. Reachability, IPv6/scheme edge cases and deployment-specific fallback are unverified. |
| Operational UI fixes | PR-only additions atop inherited APIs; reuse/retest | `CommandsView.vue` selects one/multiple/all nodes using existing command helpers; `AlertPoliciesView.vue` adds template edit; `MapView.vue` replaces schematic SVG with Leaflet/OSM tiles; topology URL alias; timestamp UTC normalization in `state/appState.ts`; sticky header and Compose `/etc/localtime` mounts. | Bulk execution/authorization and timezones need fresh tests. Map tiles require an external provider/network. These are real feature differences, not implied solely by release notes. |
| Maintenance/dependency churn | Retained history, not a new product capability | Backend lint/import/whitespace cleanups across `backend/app` and tests; manifest/lock updates and version/changelog maintenance. `alert_policy.py`, `alert_actions.py`, `security/deps.py` inspected changes are primarily formatting. | Do not claim every changed file introduces a feature. Changed backend minimum versions and frontend toolchain/router major versions need freshly provisioned parent checks. |
| Inform/adoption, transport keys, alerts, snapshots, MQTT, auth | Inherited foundations with selected PR extensions | Existing routes/services/contracts remain the management backbone. Feature command enum adds `transport_keys_sync` and `policy_sync`; detail UI adds transport-key queue action; preserved tests extend inform/core scenarios. | Existing subsystem presence and historical tests do not prove current Repeater-dev compatibility or live deployment success. No import from the primary Repeater feature checkout was performed. |

Relevant implementation documents (`docs/repeater-policy-sync-implementation.md`, `docs/repeater-sensors-glass-inform-implementation.md`) remain available as design/handoff context, not substitutes for the inspected implementation or current execution evidence.

## Baseline outcomes and gates

| Check | Result at this task boundary |
| --- | --- |
| Exact heads/ref equality/upstream ancestry | **Verified** through local Git commands above. |
| Original feature source/history recoverable | **Verified locally** at retained head and fork-tracking ref; no branch deletion/reset/rebase/squash. Remote publication durability not independently checked. |
| Unrelated active checkouts preserved | **Verified status/head discovery**; no writes or checkout operations there; pre-existing `.hermes/` entries retained. |
| PR-only source categories explicit | **Documented** above from source/diffs, including non-feature maintenance and runtime limits. |
| Feature-range whitespace | `git diff --check origin/dev...HEAD` exited **0**. This is not lint or test execution. |
| Backend lint/unit/API tests | **Parent executed after inspection:** `backend/.venv/bin/python -m pytest backend/tests -q` exited **0** (SQLite/temp-PKI fixtures; no live MQTT). `backend/.venv/bin/python -m ruff check backend/app backend/tests` exited **1**, sole E501 at `backend/app/api/routes/inform.py:65`. Python **3.14.7**; editable `backend[dev]` provisioned in this worktree only. Logs: `glass-revamp-run/task1/backend-baseline-{tests,lint}.log`. This is not PostgreSQL/Mosquitto integration certification. |
| Frontend build/type checks | **Pending parent execution**; no dependencies installed, bundles generated or browser opened here. Vite build alone must not be reported as full Vue type checking. |
| Repeater focused checks/contract parity | **Pending parent execution** against recorded Repeater dev SHA, not the unrelated primary feature checkout. |
| PostgreSQL migrations, MQTT/TLS, policy round-trip, Proxmox/live health | **Not executed / not authorized** in this task. |

No old test result, prior audit count, PR claim or changelog statement is promoted to a current pass. Parent baseline results must identify exact SHA, command, environment and real output before replacing pending entries.

## Execution limits

No code modifications, installs, approvals, application startup, containers, live hosts, keys/real environment secrets, commits, pushes, PR actions, releases, generated bundles or Ollama operations. One convenience `execute_code` call was approval-blocked before execution; it was not retried or approved. Remaining inspection used ordinary read-only Git/file tools. A status command accidentally supplied a Glass path while in the Repeater repository and returned “outside repository”; it made no change and was corrected with explicit `git -C` status reads.
