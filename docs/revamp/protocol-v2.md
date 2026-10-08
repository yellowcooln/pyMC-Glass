# Canonical Glass protocol 2 — Task3

## Scope and authority

`backend/app/contracts/v2` is the canonical offline Pydantic contract library.
Importing it does not import application startup, register HTTP routes, enroll a
node, dispatch jobs, persist outcomes or enable mutation. Existing protocol1
routes/models/tests are unchanged. Task4 owns enrollment/authentication; later
transport/dispatch tasks own delivery and durable lifecycle storage. Task3 does
**not** repair legacy route security or activate `set_mode`.

The parent-owned `glass-revamp-run/task3-decisions.md` supplies architecture and
limits. The concrete layouts below implement those decisions. Repeater should
mirror runtime validators using stdlib, not add a Pydantic runtime dependency or
assume JSON Schema alone enforces contextual rules. Model `.model_json_schema()`
is available without startup; conditional params, identity correlation, timestamp
ordering, depth/node/byte budgets and replay comparisons require runtime checks.

Transport remains outbound verified HTTPS; MQTT observation transport is separate.
Do not send these envelopes to today's unmodified Glass `/inform` endpoint.

## Common wire rules

Every v2 envelope requires explicit `type`, integer `version: 2` and `device_id`.
All contract objects forbid unknown fields and coercion. Version/count values
reject booleans, strings and floats; booleans reject integers/strings. Missing
capabilities in an inform fail; `{}` is a valid observation-only advertisement.
Capabilities are explicit declarations, never inferred from software version,
radio enumeration, display name, pubkey or telemetry.

UUIDs use lowercase canonical hyphenated text; no integer, compact or uppercase
UUID spelling. UTC timestamps require `Z` or zero offset; naive and nonzero-offset
timestamps fail (they are not silently converted). Python APIs also accept actual
UUID and UTC-aware datetime objects for typed envelope fields. Arbitrary JSON
observations/details accept only JSON values, not Python datetime/UUID/tuples.

Bounds apply both before validation and to canonical serialized output:

| Item | Limit |
| --- | --- |
| Envelope UTF-8 compact JSON | 256 KiB |
| Params / result details UTF-8 compact JSON | 64 KiB each |
| JSON nesting | 16 (root depth 0; keys do not add depth) |
| JSON nodes | 4096 (each value/container counts; keys do not count) |
| Capability entries | 128 |
| Capability versions | strict integers 1..65535; catalog currently supports 1 |
| Radios | 32 |
| Identities / sensors / plugins | 128 each |
| Results / accepted-results / queries / jobs | 64 per array |
| Resource IDs / capability names / idempotency keys | 1..64 ASCII `[A-Za-z0-9_.:-]` |
| Messages | 1024 characters |
| Display name / software/plugin version / expected revision | 1..64 characters |
| Response interval | strict integer 5..3600 seconds |

NaN/Infinity, nonstring object keys, cycles, duplicate JSON keys and unknown
envelope types fail. `parse_envelope(str_or_bytes)` checks the raw byte budget
(including whitespace), rejects duplicate keys and dispatches only known v2 types.
Already-decoded `model_validate(dict)` cannot discover duplicate source keys and
checks compact serialized size. Hostile wire input should therefore use the parser.

Models are frozen. Enrollment/device/boot identity and resource records cannot be
assigned after validation; inventory arrays become immutable tuples and capability
maps reject ordinary mutation. Opaque observation/details maps are JSON data, not
authentication or identity authorities; frozen models are not a deep security
sandbox. Validate a new object rather than mutate one. Do not bypass validation
with Pydantic `model_construct` or unchecked `model_copy(update=...)`.

## Identity, inventory and inform

`DeviceIdentityV2`: `device_id`, mutable-between-envelopes `node_name`, optional
`pubkey` (null or lowercase `0x` + 64 hex digits). Device UUID is assigned by
later enrollment and remains stable across reboots. Pubkey is metadata, not proof
of enrollment or authentication. `boot_id` changes on each node process/boot
session; it is not a replacement for device identity.

`InformV2` has:

- `type: inform`, `version`, `device_id`, `boot_id`, `sent_at`;
- `node_name`, optional `pubkey`, `software_version`;
- required `capabilities: {action_or_capability_name: positive_version}`;
- required `inventory: {radios: [], identities: [], sensors: [], plugins: []}`;
- required `telemetry: {}` and `results: []`.

Radio entries: `{id, enabled, radio_type?}`; radio_type defaults to null.
Identity entries (`IdentityV2`): `{id, kind}` with kind exactly `repeater`, `room`
or `companion`. IDs are unique across the entire identities array, even between
different kind values; they are resource IDs, not enrollment/device UUIDs.
Sensor entries: `{id, sensor_type, plugin_id?}`; sensor_type is a resource-ID
registry key. Builtin sensor registry types are independent of runtime wheel
plugins: plugin_id defaults to null and only a non-null plugin_id must reference
an inventoried plugin. Plugin entries: `{id, version}`. Resource IDs must be
unique within each inventory array, not globally across all resource categories.
IDs are node-managed durable names at baseline, not array indices, display labels
or guessed hardware addresses. Inventory order has no identity meaning. Renaming
an ID requires later durable alias/mapping work; software versions do not create
resource identity. Sensor IDs initially map configured names conservatively;
renames require future durable mapping rather than inventing continuity. Sensor
types describe registry entries and do not replace stable sensor instance IDs.
Disabled/no-radio is represented truthfully with `enabled:false`,
null radio_type, empty radios or null observation values, not fabricated RF values.

Telemetry is bounded opaque observation JSON. Fields inside it are intentionally
extensible data (as are result details), **not** additional contract fields or
implicit action params. Per-radio/per-sensor observation keys should reference
inventory IDs; the library does not invent or remap keys. Unknown structural
fields on envelopes and inventory entries fail closed.

Embedded results must match device_id and not be sent after the enclosing inform.
Duplicate `(request_id, execution_id)` results fail. A result may have an older
boot_id: later durable queues may carry an outcome through reboot.

## Action catalog, acceptance and replay

| Kind | Action / required capability | Version | Exact params |
| --- | --- | --- | --- |
| query | `diagnostic.read` | 1 | `{}` |
| query | `config.read` | 1 | `{}` |
| job (modelled only) | `set_mode` | 1 | `{mode: forward\|monitor\|no_tx}` |

No unknown action, action-version mismatch or additional params are accepted.
Action catalog extensions are parent-owned. `set_mode` is **not activated** by
parsing a valid job or advertising the capability.

Queries/jobs require `request_id`, `device_id`, `action`, `capability_version`,
`created_at`, `expires_at`, `params`; `expected_revision` is optional/null.
Jobs additionally require `execution_id` and nonempty `idempotency_key`. There is
no implicit lifetime or generated ID. `expires_at` must be after `created_at`.

`request.check_acceptance(device_id=..., capabilities=..., now=...)` requires the
exact enrolled device UUID, advertised matching capability version, and
`created_at <= now < expires_at`. It rejects missing capabilities, wrong devices,
future and expired requests. Caller must supply UTC-aware `now`; parsing does not
use an ambient clock, so historical fixtures and outcomes remain inspectable.

`job.check_replay(prior)` returns true for an identical validated job with the same
`(device_id, idempotency_key)`, false for a different scope/key, and raises for
same-key/different-envelope reuse (including different request/execution IDs,
params, revision or lifetime). Retries keep the exact original envelope. This
helper does not perform storage, at-most-once execution or crash recovery. Later
dispatch must durably reserve keys, compare prior jobs, return prior outcomes,
check expected_revision, and prevent expired requests from initiating execution.

## Structured outcomes and explicit result acceptance

`ResultV2`: `type: result`, `version`, `device_id`, `boot_id`, `sent_at`,
`request_id`, required `execution_id` (UUID for a job, null for a query), and status.
Optional/null fields: `persisted`, `applied`, `restart_required`, `error_code`,
`message`, `completed_at`; details defaults to `{}`. Null outcome flags mean
unknown/not reported; they must not be interpreted as false or success.

Statuses: `accepted`, `running`, `succeeded`, `failed`, `unsupported`, `conflict`,
`unknown`. Succeeded/failed/unsupported/conflict are terminal and require
completed_at; accepted/running/unknown forbid completed_at. Unknown describes an
unresolved execution outcome, not a fabricated terminal failure. Completion
cannot be after sent_at. `result.check_request(request)` requires exact device,
request and execution IDs and timestamps not predating request creation. Late
completed outcomes are observable after expiry; expiry prevents new execution,
not delivery of historical outcomes. The library does not enforce transition
history or presume persisted/applied flags from status.

`ResponseV2`: `type: response`, `version`, `device_id`, `boot_id`, `sent_at`,
`interval_seconds`, required `accepted_results`, `queries`, `jobs` arrays.
Accepted result entries are exact `{request_id, execution_id}` pairs; no implicit
"all received" acknowledgement. Duplicate acceptance pairs/request IDs/execution
IDs and cross-device requests fail. `response.check_inform(inform)` requires
matching device/boot, nondecreasing sent_at and acceptance pairs actually offered
in that inform. A producer must retain results until these IDs are explicitly
acknowledged by the later authenticated server; Task3 does not persist/ack them.

## Safe legacy observations and negotiation

`adapt_legacy_observation(raw)` requires explicit integer version1/type inform and
bounded finite JSON, then preserves a detached copy of the original observations.
It does **not** pass legacy payloads through `InformRequestV1`: current producers
emit frequency/bandwidth zero for disabled radio and strict v1 rejects these.
The adapter retains those zeros and explicit `radio:null` without inventing RF
values, sensor units, stable resource IDs, enrollment UUIDs or capabilities.
Wrapper fields are always `protocol:1`, `device_id:null`, `boot_id:null`,
`capabilities:{}`, `control_allowed:false`. Unknown legacy observation keys stay
observations, never v2 structural fields. Legacy mutation remains unavailable
until explicit trust/capability migration. Existing v1 endpoint behavior is not
changed by this adapter.

`negotiate_protocol(device_id=..., operational_credentials=bool)` reports v2
eligibility only if a valid explicitly configured enrolled UUID and explicit true
operational-credential readiness are both provided; otherwise v1. Invalid supplied
UUIDs fail rather than being repaired. This is not credential validation, a live
transport switch, or software-version negotiation. Repeater integration stays
inactive/default-legacy until enrollment and transport tasks activate it.

## Canonical parity artifacts

The historical `proposed_v2_*.json` names are retained for baseline v1 rejection
tests, but their contents are now actual sanitized canonical Task3 envelopes:
inform, query, job, result, response. The read-only inform intentionally does not
advertise set_mode; the result records unsupported capability/no mutation. The
response acknowledges that result only when it is included in an inform (the
context-binding test constructs that batch explicitly). They are not production
captures or evidence of activated control.

`backend/tests/test_protocol_v2.py` validates round-trip/schema shape, strict
rejection cases, bounds, contextual checks, immutable IDs/capabilities, replay,
negotiation and legacy preservation. Existing fixture tests continue to assert
v1 rejects the replaced v2 examples. Repeater must copy these exact fixtures and
mirror runtime rejection/acceptance behavior; publication and cross-repository
parity verification belong to the parent, not an unchecked schema copy.

### Concrete choices beyond the parent decision text

These implementation details are explicit, not silent architecture changes:
canonical UUID spelling; zero-offset UTC only; ASCII resource-ID grammar;
capability versions capped at 65535; identities/sensors/plugins capped at 128;
required inventory/response arrays; explicit identity IDs/kinds; builtin sensor
types independent of plugins with non-null plugin reference validation; nullable query
execution ID; terminal status/timestamp rules with `unknown` unresolved; exact
same-envelope idempotency retries; opaque telemetry JSON rather than inferred
hardware metric schemas; no fixed TTL or command activation. Review these with
the matching Repeater implementation before publishing. No parent decision has
been intentionally relaxed or replaced.
