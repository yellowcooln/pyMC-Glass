# Device certificate rotation: server issuance slice

## Scope and activation

`POST /device/certificates/renew` is an authenticated, public-only CSR issuance
endpoint. This is Task5(c)'s server slice, **not full Task5 completion**. It does
not enable a UI action or advertise a node capability. No report endpoint or
node installation helper is introduced here.

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
- 409: pending renewal, changed-key replay, stale/revoked replay, or database
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
- A different request ID in the same enrollment returns 409 while the prior
  rotation is unreported and unexpired. A node-reported or expired row may be
  replaced by new issuance, retaining Certificate history.
- A newer enrollment hash may replace the obsolete row, but cannot retrieve
  the old response. The old enrollment's bearer loses authority through the
  existing enrollment flow.

Certificate history (serial/CN/PEM hash), the bounded rotation response,
credential serial/key hash, parent serial/expiry, and sanitized
`device_certificate_issued` audit are committed together. The audit's target
is the immutable node, its requester is a machine, and no human user ID or
request-provided text is attributed to it. Issuance resets the rotation's
`node_reported_*` fields to null.

## Explicit remaining limits

Issuance does **not** prove installation, reconnect, cutover, or broker ownership.
It intentionally does **not** revoke the previous certificate. The old
certificate remains active until explicit cutover/revocation is delivered and
real-broker tested. Existing enrollment/revocation behavior is unchanged.

Future node reports are node assertions, not authoritative broker verification.
The parent-owned next slice must define that endpoint and durable node pending
request/key state, stage/validate/install handling, wrong-key/expired/partial
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
