# Device enrollment and inform authority (Task4)

Operational HTTP authority is a per-device random bearer credential over verified
HTTPS, not HTTP mutual TLS. MQTT certificate authentication is separate. The
backend checks the ASGI HTTPS scheme, never `X-Forwarded-Proto` itself and never
client-certificate/device identity headers. A terminating proxy must be explicitly
trusted by Uvicorn for scheme forwarding; keep backend ports private. Nodes must
verify the HTTPS server certificate. Application tests do not prove a live
proxy's TLS configuration.

## Administrator and node flow

1. An authenticated **admin** calls
   `POST /api/adoption/{repeater_id}/enrollment` over HTTPS. Only adopted,
   connected, or offline nodes qualify. Approval binds the immutable existing
   UUID, current exact node name and radio public key. The response supplies
   `device_id`, `enrollment_token`, and `expires_at`; the random 256-bit token is
   displayed once, expires in 15 minutes, and replaces outstanding approval.
2. Transfer approval out of band through secure SSH/configuration management.
   The node generates its own RSA key (at least 2048 bits), signed PEM CSR, and
   a separate operational credential using `secrets.token_urlsafe(32)`.
3. The node calls HTTPS `POST /enroll` with exactly `device_id`, `node_name`,
   `pubkey`, `enrollment_token`, `operational_token`, and `csr_pem`.
   The streamed request limit is 16 KiB; secrets are exact URL-safe strings
   of 43–128 characters. CSR signature and key strength are validated before
   approval consumption. Validation errors do not echo input secrets.
4. Approval is consumed by a conditional SQL UPDATE requiring matching hash,
   identity binding, unexpired deadline and null consumption timestamp. PKI
   issuance, credential replacement, certificate metadata and consumption commit
   together. Failure rolls back consumption. The expiry check uses the time after
   lock acquisition and CSR validation, not the time before a lock wait.
5. The response contains only `device_id`, `client_cert`, `ca_cert`,
   `cert_serial`, and `expires_at`; never a private key or operational secret.
   Submitted subject/attributes/extensions are not copied. Certificate identity
   is CN `device:<uuid>` and URI SAN `urn:openhop:device:<uuid>`, with clientAuth,
   non-CA and constrained key usage. DB fingerprint is SHA256 of DER SPKI.

Both token tables store SHA256 hashes only. Secret issuance responses carry
`Cache-Control: no-store`; application audit entries contain identifiers only.
Do not enable request-body or Authorization-header logging at the proxy/server.

## Operational integration and revocation

`app.security.devices.get_current_device` checks HTTPS, bearer hash, credential
revocation and current adoption status. It returns the Repeater identified by
immutable ID. `bind_device_identity` checks claimed ID and optional exact public
key, never display name. Rename does not change bearer authority. Missing
credentials cannot be replaced by identity headers.

Legacy `/inform` allows anonymous discovery only for pending devices without
results. Adopted/connected/offline/rejected identities require valid credentials
before telemetry, result ingestion, policy evaluation, provisioning or dispatch.
It no longer issues certificates/private keys. `/inform/v2` authenticates and
persists the latest bounded observation; it does not dispatch jobs/queries or
acknowledge results. See [inform authentication](inform-authentication.md).

Admin HTTPS `POST /api/adoption/{repeater_id}/credentials/revoke` revokes the
credential, invalidates outstanding approval, and marks certificate metadata
revoked. Adoption rejection invokes the same invalidation in its transaction.
Re-adoption does not restore revoked credentials. Certificate metadata revocation
is not a deployed broker CRL/revocation implementation.

Explicit re-enrollment replaces the credential and marks previous certificate
metadata revoked; the new operational token must differ from the current token.
If a response is lost, obtain new administrator approval; there is no anonymous
recovery. Existing devices receive no automatic credentials or migration bypass.

## Transaction serialization and async boundary

Only bounded body reading/decoding is async. `/enroll`, `/inform`, and `/inform/v2`
transaction handlers are synchronous FastAPI threadpool functions; synchronous
SQL, row-lock waits and cryptographic work do not run on the event loop. Body
reading finishes before operational authentication, opening its DB session or
acquiring device row locks. A slow client cannot hold the device lock while
streaming its body.

All authority-changing paths use the same PostgreSQL serialization point:
`SELECT ... FOR UPDATE` on the existing Repeater first, then credential/enrollment
rows (credential before enrollment when both are needed). Enrollment approval,
credential revocation, adoption/rejection and legacy/v2 inform share this parent
lock. Authentication initially looks up a bearer hash to an ID **without locking
its credential**, locks and refreshes the Repeater with `populate_existing`, then
locks and refreshes the credential and checks its hash, revocation and adoption
status. A stale SQLAlchemy identity-map object is never final authority. Deleted
or replaced identities are denied rather than recreated by authentication.
Anonymous discovery also locks and refreshes existing identities before deciding
that they are pending. Locks are held through operational writes, result ingestion,
queue dispatch and commit; failures release them through rollback/session close.

The serial semantics are deliberate: if an inform acquires authority first, it
may finish its authorized operation before the waiting revoke/reject commits.
If revoke/reject wins the parent lock, the subsequent inform sees the committed
revocation/status and returns 401 without mutation, results or dispatch. This is
not retroactive cancellation of already-authorized operations. Credential rotation
likewise denies the old token after waiting. No application-thread mutex is used
as a substitute for database serialization.

## Evidence and remaining verification

SQLite application regressions use real HTTPS TestClient/ASGI requests, synthetic
credentials, temporary PKI, real login and enrollment. They cover one-use replay,
identity binding, expiry/replacement, rename, revoke/reject denial, CSR/certificate
constraints, rollback, secret-safe errors and bounded bodies. Body barriers verify
that revoke/reject can commit during paused legacy/v2 inform and enrollment
streams, and that the resumed operation is denied without side effects. Separate
barriers verify that a paused synchronous transaction leaves the event loop
responsive. Stale identity-map regressions fail against the previous auth helper.
These tests **do not prove PostgreSQL row-lock behavior**.

An opt-in PostgreSQL test exercises real legacy/v2 transaction handlers with two
barrier-controlled SQL transactions in both winning orders. It checks an actual
`pg_stat_activity` Lock wait, refreshed denial, queue/telemetry preservation, and
final revoked/rejected state. It creates and drops only a cryptographically unique
`glass_auth_test_<uuid>` schema; no app startup, live credentials or public-table
writes. Run from `backend` against an **isolated disposable test database**:

```sh
GLASS_DEVICE_AUTH_PG_TEST=1 \
DATABASE_URL='postgresql+psycopg://test_user:TEST_PASSWORD@localhost/isolated_test_db' \
.venv/bin/python -m pytest tests/integration/test_device_auth_concurrency.py
```

Without explicit opt-in these tests skip. The parent executed all eight tests against
PostgreSQL16 in the isolated CT301 candidate (8 passed in8.82seconds), including actual
Lock waits in both winning orders. The rebuilt HTTPS/nginx candidate passed50 real
HTTP/certificate checks: single-winner bootstrap and enrollment, cross-device denial,
revocation, body bounds and untrusted forwarded-scheme rejection. Its issued CSR
certificate passed the actual Repeater validator. Local SQLite/skips are not the
PostgreSQL evidence. Full revamp control dispatch, MQTT ownership/rotation, application
upgrade/restore and authenticated browser acceptance remain later gates; live
certificate-issuance-failure injection was not performed.
