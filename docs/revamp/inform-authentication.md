# Inform authentication and migration limits

`/inform` no longer treats node name or radio public key as operational authority.
Existing adopted, connected and offline nodes must be enrolled through the
admin-approved CSR enrollment flow before their next operational inform. There
is no anonymous credential recovery or compatibility bypass. See
[device enrollment](device-enrollment.md).

## Legacy discovery and operational traffic

- Anonymous v1 informs can create pending-adoption observations and rediscover
  only the exact pending node-name/public-key pair. A collision is rejected.
- Discovery cannot submit command results, receive commands, provision MQTT or
  issue certificates. It never overwrites an adopted or rejected node.
- Authenticated v1 informs require HTTPS and a per-device bearer token. The
  credential resolves immutable `Repeater.id`; the reported public key must
  match exactly. Display-name changes do not switch identities, and a name
  belonging to another node is rejected rather than used as a fallback.
- Authenticated v1 retains legacy telemetry, command-result ingestion and queue
  dispatch. This is not v2 command admission or a durable execution ledger.
- Inform never returns a server-generated private key or certificate-renewal
  response, even with valid credentials. Reported certificate expiry cannot
  overwrite issued certificate metadata. Certificate issuance is CSR-only via
  explicit enrollment; operational rotation remains a later transport task.
- Rejection revokes the device credential, outstanding enrollment and certificate
  metadata in the rejection transaction. Re-adoption does not revive the token.

## Observation-only v2

`POST /inform/v2` requires HTTPS and the same operational bearer credential.
Canonical v2 validation binds `device_id`, optional `pubkey`, and a required
`boot_id`. Names are display metadata, not authentication. Both routes enforce a
256 KiB streamed body limit and reject duplicate JSON keys and over-budget JSON.

Migration `0016_device_observations.sql` stores the latest received advertisement
per device: boot ID, sent timestamp, capabilities, inventory and telemetry.
Capabilities are observations, not permission to execute actions. Results are
validated but **not persisted or acknowledged**: the typed response always has
empty `accepted_results`, `queries` and `jobs`, and echoes the inform boot ID.
The sender must retain any pending results; do not activate v2 dispatch based on
this endpoint. Observation receipt does not mark a node connected or update the
legacy telemetry/history projections. This table is a last-received snapshot,
not a timestamp-ordering or anti-replay ledger.

## HTTPS deployment boundary

Only the ASGI request scheme is used for HTTPS enforcement. `X-Client-Cert`,
`X-Device-Id` and similar identity headers confer no authority. The application
does not inspect `X-Forwarded-Proto` itself: production scheme forwarding must be
accepted only from an explicitly trusted TLS-terminating proxy, with backend
network access restricted. These routes do not implement HTTP client-certificate
mTLS. Trusted-edge scheme configuration, real proxy tests, PostgreSQL concurrency
verification and node-side migration remain separate rollout gates.
