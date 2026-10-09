# Task 7 — durable node execution and result reconciliation

Delivered as one Glass + Repeater source milestone. The original queued worker remains stopped. No live repeater code, service, configuration, enrollment, radio or RF operation was changed.

## Behavior

- A private bounded SQLite ledger durably records receipt, START and exact outcomes before proceeding. Duplicate execution IDs cannot repeat an admitted effect. Results remain pending until an exact acceptance ID/body digest is received.
- An interrupted START becomes `unknown`, not an automatic retry. A receipt that expires before START resolves without calling the executor. Cancellation is effective only before START; afterward the store reports too-late.
- Restart evidence requires actual trusted local boot/version/revision/readiness/uptime observations. Missing or timed-out evidence remains unknown. Earlier proof survives an outstanding acknowledgment; it is not replaced by a later timeout.
- Authenticated v2 informs consume the ledger and validate the entire response before acknowledgments or deliveries. Configuration/cancellation races drain the owned worker and preserve its lock. There is no authenticated-to-legacy fallback.
- Migration0020 retains issued lease history. A known older lease can receive a `superseded` archival receipt without changing the current command's outcome. Exact retries retain the same receipt and do not consume another audit/phase.
- Direct review found and fixed START admission using a clock sampled before a potentially blocking runtime guard. The clock/lease check now runs after the guard, immediately before START. A failing regression reproduced actual execution after lease expiry; the corrected test proves no callback runs.

## Actual verification

- Node ledger: the initial complete 34-case suite passed, including real subprocess SIGKILL at persistence boundaries, concurrent process locking, exact acknowledgment, bounded retention, cancellation and the new guard/deadline regression. Direct review then corrected successful read-query retention: acknowledged terminal reads age out after their request window while job tombstones keep seven days and unacknowledged/unknown records remain. The acknowledged-read case first reproduced the retention issue; the final ACK/no-ACK coverage and affected 8-case retention/deadline/replay/capacity selection passed with zero failures/errors/skips (28 other cases deselected, not falsely called a full new run).
- Node handler/contracts/legacy handler: 142 cases passed, zero failures/errors/skips.
- Backend reconciliation/lifecycle/protocol: 185 cases passed, zero failures/errors/skips. One additional boundary test confirms an issued query becomes unknown at its deadline and a later original-lease result legitimately resolves that uncertainty; it did not require a production code change.
- Real PostgreSQL: 20 cases passed, zero failures/errors/skips, using disposable schemas in the isolated test database. Includes archive/current-result races, duplicate receipts, capacity, current unknown-result races, existing claim deadlines and migration0001–0020 replay.
- Actual candidate HTTPS: the real handler received a diagnostic query, persisted its result, discarded an actual server-committed acknowledgment at the client boundary, then a new handler boot replayed the unchanged result and retired it after the exact acknowledgment; authoritative backend status was checked (4 checks).
- Actual stale-result HTTPS: waited for the real 60-second synthetic query lease to expire, another polling client received the new lease, and the original handler archived only its old durable result. The new command remained queued without a claimed result (3 checks). No database-clock mutation was used for this test.
- Scoped Ruff lint, new-file formatting, strict Node OpenAPI contracts and both Git diffs passed. New-file formatting was proven AST-equivalent to the executed source; no repeated full certificate suite for formatting.
- Six deployed candidate backend files match current source hashes. Current node source ran only in synthetic test containers. Test containers were removed after collecting results.

Evidence: `glass-revamp-run/lab/direct-task7-{ledger-green,handler-green,backend-focus,pg}.xml`, guard RED output and format equivalence JSON, plus the actual HTTP probe outputs. The first ledger foreground command timed out at180seconds while its isolated test continued; its complete JUnit was subsequently retrieved (34 cases,191.953seconds). It was not rerun or counted as a pass from the timeout. Other owned test-container exit codes were collected explicitly. Deprecation/read-only pytest-cache warnings are not hidden.

## Limits and next dependency

Production execution advertises only implemented `diagnostic.read` v1. Other job executors are not enabled by this milestone. Disruptive effects and restart/readiness evidence were tested using synthetic callbacks and separate real killed processes, not physical restarts/updates/RF. Client-boundary lost-response injection is not claimed as an actual dropped TCP packet. Actual domain adapters and their authoritative readback remain Tasks8/12–17.

The complete backend/node suites are reserved for the coherent M1 gate after Task8; the focused passing results above are not falsely labeled a new whole-project green run. Task8 (transactional config/policy/key writes and truthful readback) remains the next plan dependency. M1 and the full revamp are not yet complete.
