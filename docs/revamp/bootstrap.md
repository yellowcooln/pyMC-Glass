# One-time administrator bootstrap

The first administrator is now guarded by a durable database claim, not a
count-then-insert decision. `bootstrap_claim` contains only the fixed primary key
`1` and a UTC creation timestamp; it stores no passwords, tokens or other secrets.

## Transaction boundary

`POST /api/bootstrap/admin` validates password length before acquiring the claim.
The shared `claim_first_admin` helper inserts and flushes claim `1` before creating
any user. Competing claim inserts conflict on the primary key: the losing API
request rolls back and receives HTTP 409. After acquiring the claim the helper
also checks for existing users and rolls back with HTTP 409 if any exist.

Claim, administrator and audit record commit together. Hashing, user flush, audit
or commit failures roll the transaction back, leaving bootstrap retryable.
`GET /api/bootstrap/status` reports bootstrap unavailable when either users or the
claim exist. Removing users does not reopen unauthenticated administrator creation.
Existing authenticated user-management and credential/HTTPS enforcement are unchanged.

## Startup seed and upgrades

Startup seeding uses the same claim helper and transaction boundary as manual
bootstrap, preventing two seed processes or seed/API races from creating multiple
first administrators even when they use different emails. Seed enablement,
configured credentials, validation and audit behavior remain in place; this change
introduces no default secret. Production deployments should supply their own seed
credentials or disable startup seeding and perform manual bootstrap over HTTPS.

Migration `0017_bootstrap_claim.sql` adds the table and claims `1` on databases
which already contain users, without modifying any users, roles or password hashes.
Earlier migrations are untouched. Empty databases remain eligible for bootstrap.
There is no automatic reset or deletion of a committed claim; account recovery
must not use the unauthenticated bootstrap endpoint.

## Verification and remaining scope

`backend/tests/test_bootstrap_atomic.py` covers successful claims, duplicate
bootstrap, existing-user preservation, migration backfill/idempotence, password
validation, injected hashing/user-flush/audit/commit failures, startup seed behavior
and concurrent distinct-email API/API and API/seed calls using separate sessions
against a real SQLite file. SQLite tests are not PostgreSQL evidence.

Real PostgreSQL process-concurrency verification remains pending in the parent lab:
run distinct-email API/API, API/seed and seed/seed races against an empty migrated
database; verify exactly one user and one claim, API losers returning 409, and
rollback/retry on injected failure. Run with the supported default READ COMMITTED
isolation level. No special SQLite-only locking or fake concurrency claims are used.
