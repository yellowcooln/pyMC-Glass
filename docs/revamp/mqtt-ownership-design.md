# Managed MQTT ownership — pure foundations and ingress wiring

**Status: strict DB ingress wired; broker policy not activated.** `mqtt_ingest.py`
uses the strict v2 parser with no legacy fallback. These tests do not prove
publisher certificate ownership or production broker authorization. No config,
compose, model, migration, enrollment, revocation or renewal endpoints change here.

## Strict identity parser

`app.services.mqtt_identity.parse_managed_message(topic, payload_bytes)` returns
`ManagedMqttMessage` or raises `ValueError`, with no legacy fallback or side effects.
Accepted record catalog:

- `glass/device:<canonical UUID>/packet`
- `glass/device:<canonical UUID>/advert`
- `glass/device:<canonical UUID>/event/<ResourceID>`

Event ResourceIDs follow the existing v2 character grammar `[A-Za-z0-9_.:-]+`,
1–64 characters; no slashes or MQTT wildcards. Current producer events are
`noise_floor` and `crc_errors`; bounded future event names remain permitted.

The envelope requires exactly `version` (integer 2), `type`, `device_id`, `topic`,
`node_name`, `timestamp` and object `payload`. Events additionally require matching
`event_name`; packet/advert must omit it. Unknown envelope fields are rejected.
Topic owner and canonical envelope device ID must agree, and the envelope topic,
record type and event name must exactly match the actual publication topic.
`node_name` is only a nonempty display string: it is never used to authorize or
resolve the device, and may differ from any stored mutable name.

Limits: UTF-8 input at most 256 KiB; JSON root at depth 1, maximum depth 16;
maximum 4096 nodes (root, values and object keys all counted). Duplicate members,
nonfinite numbers including overflowed exponents, invalid Unicode scalars, invalid
JSON and missing input fail closed. Payload canonicalization uses sorted keys,
compact separators, UTF-8 characters and no nonfinite floats. Incoming whitespace
and key order need not already be canonical. Timestamp is explicit Unix seconds
(non-boolean number) or timezone-aware ISO datetime, normalized to UTC, bounded
by the Unix epoch and Python's representable datetime range; no receipt-time
fallback. No timestamp freshness or replay claim is made.

Parsing alone does not prove publisher certificate identity. DB ingestion now
resolves Repeater.id from device_id and requires adopted/connected/offline state
and a non-revoked DeviceCredential. It uses the shared parent-row lock_repeater
before locking and refreshing the credential, holding authority locks through the
analytics commit, consistently with HTTP revocation. No MQTT bearer token is
invented or checked: credential existence is enrolled-device eligibility, not
proof of the publishing TLS peer. SQLite unit tests do not prove PostgreSQL lock
serialization under concurrency.

The subscriber uses the fixed glass namespace, ignores configured mutable topic
prefixes, and drops oversized messages before queueing. Packet dedup uses stable
device_id, not display name. UI telemetry node_name comes from the locked DB row;
envelope node_name cannot select or rename a repeater. Advert payload node_name
still describes the observed neighbor. Existing packet persistence, topology
observations/samples and hourly rollups remain, including UTC normalization when
SQLite returns naive stored datetimes. Legacy MQTT must reenroll/use v2; legacy
HTTP inform observations remain separate and unchanged.

Until explicit broker ACLs bind certificate CN device:<uuid> to its own managed
topic, a publisher able to forge BOTH topic and envelope for another eligible
device remains indistinguishable at the subscriber. This wiring rejects mismatch
and ineligible records but is not complete broker identity enforcement. Task5(d)
must deploy and prove mTLS/ACL/CRL behavior before claiming that boundary secure.

## Pure policy builders

`mqtt_broker_policy.build_acl(active_device_ids)` returns deterministic ACL text,
sorted by canonical UUID. Inputs are caller-selected eligible enrolled identities,
not a DB lookup. Duplicate or invalid IDs and more than 1024 IDs fail closed.
Backend CN `openhop-glass-backend` has **read only** `glass/#`; each explicit
`device:<uuid>` user has **write only** `glass/device:<uuid>/#`. No anonymous
permissions, device subscriptions, global write permissions or `pattern` grants.

`build_crl(ca_certificate, ca_private_key, revoked, now)` takes already loaded
cryptography objects, serial-hex/aware-UTC datetime tuples and explicit aware UTC
`now`, returning a signed `x509.CertificateRevocationList`. It does not read any
key paths (especially production root signing keys). It validates CA/key match,
critical CA BasicConstraints, key-cert-sign and CRL-sign usage, and current CA
validity. Serial strings are bounded hex, positive, at most 159 bits; duplicate
numeric serials and more than 4096 entries fail. Revocation times must be UTC,
within X.509's supported range and no later than `now`. Entries are numerically
sorted, issuer is CA subject, last update is `now` at X.509 second precision,
next update is at most seven days later and no later than CA expiry. RSA/EC/DSA
use SHA-256; Ed25519/Ed448 use their native signature algorithm. Signature bytes
need not be deterministic for randomized signing algorithms.

## Pending wiring and operational proof (parent-owned)

- Node stable-ID v2 publisher and verified certificate bundle materialization.
- Operational DB-to-broker eligibility/revocation policy providers.
- Broker-only mount excludes root CA and backend private keys; bounded atomic
  ACL/CRL publication, broker-owned content-hash watcher and child SIGHUP reload.
- Failure injection and explicit DB/file transaction-gap reporting. Failed policy
  publication must not silently claim effective broker revocation.
- Real isolated two-device Mosquitto proofs: cross-device denial, device read
  denial, revocation of already-connected publishers by ACL removal, new revoked
  connection denial by CRL, renewal/reconnect and policy-reload failure behavior.
  A signed CRL alone does not prove those properties or disconnect TLS sessions.

Unit coverage uses synthetic real CA certificates and signature verification.
No broker process, live host, service installation or policy activation is involved.
