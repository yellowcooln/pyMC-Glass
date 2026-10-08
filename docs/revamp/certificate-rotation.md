# Device certificate rotation: server issuance and node assertion

## Scope and activation

`POST /device/certificates/renew` is an authenticated, public-only CSR issuance
endpoint. This is Task5(c)'s server slice, **not full Task5 completion**. It does
not enable a UI action or advertise a node capability. The authenticated report
endpoint records only a node assertion; no node installation helper is introduced.

The node must generate and retain its own private key and persist a canonical
UUID `request_id` with the corresponding CSR before sending a request. The
server never generates, receives, persists, or returns the node private key.
Persisting the pending request/key on the node remains future work.

## HTTP contract

Use HTTPS and an operational device bearer credential from enrollment. Only the
ASGI URL scheme is accepted as transport authority; `X-Forwarded-Proto`, device
ID headers, certificate headers, display names, and human JWTs confer no device
authority. Trusted proxy configuration is deployment-owned.

The JSON object has exactly these fields:

```json
{
  "device_id": "00000000-0000-0000-0000-000000000001",
  "request_id": "00000000-0000-0000-0000-000000000002",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n"
}
```

Both IDs must be canonical lowercase UUID strings. `device_id` must match the
immutable bearer-owned node, irrespective of its display name. The streamed
body is bounded to 16,384 bytes, including requests without Content-Length;
ingestion has a 10-second total deadline (not a per-chunk idle timeout).
After the HTTPS check, bounded ingestion and schema validation precede bearer
authentication: malformed or oversized bodies can return 422/413 even with
invalid credentials. No database session or authority lock exists while
awaiting body chunks; timeout/cancellation leaves no such resources to release.
`csr_pem` is bounded to 14,000 characters and validated by `PkiService` (ASCII
PEM, valid signature, RSA of at least 2,048 bits). CSR subjects/extensions do
not control the resulting identity: existing PKI issuance sets the immutable
`device:<uuid>` CN and device URI SAN.

A 200 response has exactly `device_id`, `request_id`, `client_cert`, `ca_cert`,
`cert_serial`, `expires_at`, `fingerprint_sha256`, and `state`. `state` is always
`issued`. The fingerprint is lowercase SHA-256 hex of the DER leaf certificate,
not the CSR key or PEM hash. Successful responses include `Cache-Control:
no-store`.

Errors are sanitized and do not echo CSR/request values:

- 400: HTTPS required.
- 408: total streamed-body ingestion deadline exceeded.
- 401: missing/invalid/revoked device credential, ineligible parent status, or
  body identity mismatch.
- 413: streamed body exceeds the byte bound.
- 422: malformed/extra fields, noncanonical IDs, invalid CSR or CSR bounds.
- 409: rate-limited or pending renewal, changed-key replay, stale/revoked replay, or database
  integrity conflict.
- 503: issuance or transaction failure; the transaction is rolled back.

Expired current certificates do not disable bearer-authenticated HTTPS renewal.

## Locking, replay and persistence

The synchronous route runs crypto and SQL in FastAPI's worker pool, not on the
ASGI event loop. Route-local dependencies explicitly gate session creation on
successful bounded-body validation, then call the existing `get_current_device`
with that session. This ordering is a dependency edge, not parameter ordering.
Revocation committed during ingestion is honored by the subsequent locked
authentication. The existing `get_current_device` function locks/refetches
the parent repeater before the credential, rechecking token, revocation and
parent status under the lock. The route locks the rotation next, with the same
session and transaction. PostgreSQL lock/concurrency proof remains a separate
parent-owned gate; SQLite tests do not establish it.

One `DeviceCertificateRotation` row per node stores the latest public response,
request ID, credential hash, CSR public-key hash, previous serial, serial,
expiry, and leaf fingerprint. Neither the bearer token nor the CSR is stored.

- Same enrollment hash + request ID + CSR key + current serial returns the
  original public response without new issuance or another audit entry.
- Reusing that request ID with another key, changed credential/parent serial,
  missing certificate history, or revoked issued certificate returns 409.
- A different request ID in the same enrollment within 3,600 seconds of
  actual server issuance returns 409 `Certificate renewal rate limited`, unless the current
  rotation has expired. This check precedes the pending-report check. Beyond
  that interval, an unreported and unexpired rotation still returns 409 pending;
  a reported or expired row may be replaced, retaining Certificate history.
  Exact retries remain eligible even when expired (metadata only, not a usable
  certificate; the future node helper must reject expiry).
- A newer enrollment hash may replace the obsolete row, but cannot retrieve
  the old response. The old enrollment's bearer loses authority through the
  existing enrollment flow.

New rotation rows store actual server UTC in `DeviceCertificateRotation.issued_at`,
captured immediately after PKI issuance returns. `Certificate.issued_at` retains
the X.509 `not_valid_before` validity start, which PKI backdates by five minutes;
it is not the real issuance time. Existing history and rotation timestamps are
not rewritten. For legacy rotation rows whose timestamp exactly equals the
stored public leaf's UTC validity start (X.509 whole-second precision), the
rate-limit anchor is conservatively that validity start plus five minutes.
Otherwise the anchor is the rotation's actual issuance timestamp. At 3,599
seconds a different request is rate limited; at exactly 3,600 it passes this
check, but an unreported/unexpired rotation still remains pending. Invalid stored
leaf metadata on this check yields sanitized 503 without writes; exact retries
and expiry recovery preserve their existing ordering and bypass this check.

Certificate history (serial/CN/PEM hash), the bounded rotation response,
credential serial/key hash, parent serial/expiry, and sanitized
`device_certificate_issued` audit are committed together. The audit's target
is the immutable node, its requester is a machine, and no human user ID or
request-provided text is attributed to it. Issuance resets the rotation's
`node_reported_*` fields to null.

## Authenticated node report

`POST /device/certificates/report` uses the same HTTPS machine bearer authority.
Its exact JSON fields are `device_id`, `request_id`, `cert_serial`,
`fingerprint_sha256`, `boot_id`, and `connected`. All three IDs are canonical
lowercase UUIDs; serial is positive lowercase hex with at most 40 characters;
fingerprint is exactly 64 lowercase hex characters; `connected` must be literal
JSON `true` (not 1 or a string). No timestamp, PEM, secret, or extra field is accepted.
The streamed body is bounded to 2,048 bytes and a total 10-second deadline,
with sanitized 422/408/413 responses before any session or authority lock opens.

The node asserts that it readback-installed this leaf and received an actual
successful current MQTT connect callback. The server cannot verify that claim.
Under parent-first locked bearer authentication and a refreshed locked rotation,
the enrollment generation, request ID, credential/parent/rotation serial, DER
fingerprint and unexpired rotation must match. Associated Certificate history
must exist and be unexpired and nonrevoked. Invalid device authority/body identity
is 401; stale or conflicting reports are 409 without report or audit updates.

The first report sets server UTC `node_reported_at` and `node_reported_boot_id`
atomically with a `device_certificate_node_reported` audit targeted at the node,
containing only machine requester, serial and boot ID. An exact same-boot retry
does not refresh the timestamp or add an audit. A different boot for the same
current leaf refreshes both with one new audit. Old reports cannot resurrect a
rotation replaced by subsequent issuance. Transaction failures roll back and
return sanitized 503; integrity conflicts return 409.

The exact no-store 200 response fields are `device_id`, `request_id`,
`cert_serial`, `accepted: true`, and `state: "node_reported"`. This is only an
authenticated node assertion, not broker proof, cryptographic connection proof,
or server verification of installation. Reports never revoke the old leaf.

Manual administrator reenrollment creates another generation and bypasses the
per-generation interval; expired rotations allow recovery without waiting.
Neither is a forced-rotation UI, and the rate bound does not globally cap history
across administrative reenrollments or repeated expiry recovery.

## Explicit remaining limits

Issuance does **not** prove installation, reconnect, cutover, or broker ownership.
It intentionally does **not** revoke the previous certificate. The old
certificate remains active until explicit cutover/revocation is delivered and
real-broker tested. Existing enrollment/revocation behavior is unchanged.

Node reports are assertions, not authoritative broker verification.
The next node slice must implement durable pending request/key and report outbox
state, stage/validate/install handling, wrong-key/expired/partial
write/same-path reload and reconnect behavior. Real Mosquitto mutual-TLS
ownership, CRL revocation, and reconnect tests are still mandatory before full
Task5 acceptance. No PostgreSQL or broker result is claimed by this slice.

## Focused verification

From the repository root, using the existing virtual environment:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_device_certificate_rotation.py -q
backend/.venv/bin/python -m ruff check backend/app/api/routes/device_certificates.py backend/app/schemas/device_certificates.py backend/app/main.py backend/tests/test_device_certificate_rotation.py
```

Tests use real temporary PKI, HTTP requests and SQLite transactions. They cover
exact public retry/no private key, immutable identity despite rename, bearer
and HTTPS rejection, invalid CSR/signature/key strength, bounded chunked bodies,
pending/key/stale/revoked conflicts, expired/reported row replacement,
enrollment-generation separation, rollback and off-event-loop issuance.
Actual asynchronous HTTP streams also verify body completion before session
creation/parent locking, revocation during ingestion (401 and no issuance),
total deadlines for both stalled and continually arriving chunks, cancellation,
and rejected bodies without opening database sessions. These establish request
ordering, not PostgreSQL row-lock contention behavior.
Report regressions cover exact/same-boot/new-boot responses and audits, malformed
and bounded bodies before sessions, stale identity/generation/serial/fingerprint,
revoked/expired history and device authority, atomic rollback, and reuse the real
asynchronous stream/deadline/cancellation and revoke-during-ingestion tests.
Renewal tests cover rate-before-pending, interval eligibility, exact retries,
expired recovery, and obsolete reports after subsequent issuance.
Controlled module-local clocks exercise real PKI's backdate at exactly 3,599 and
3,600 seconds for reported and pending rows, both new timestamps and legacy
aliases, while preserving certificate history's validity timestamps. The opt-in
PostgreSQL interval suite repeats these thresholds with committed transactions,
exact response retries, issuance/audit counts, and expired recovery.
