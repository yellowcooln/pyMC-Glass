# Repeater implementation handoff: Glass `policy_sync`

## Goal

Make openHop Repeater dev execute the `policy_sync` command queued by openHop Glass.
Glass is already pre-staged to build, validate, store, and queue Repeater Policy Engine templates.

## Glass command shape

Glass queues a normal inform-response command:

```json
{
  "type": "command",
  "command_id": "<uuid>",
  "action": "policy_sync",
  "params": {
    "policy": {
      "enabled": true,
      "default_action": "allow",
      "rules": [],
      "objects": {
        "channel_hash_groups": {
          "blocked_channels": ["0x12"]
        },
        "pubkey_groups": {
          "trusted_relays": ["0xaabbccdd"]
        }
      }
    },
    "mode": "replace",
    "validate_only": false,
    "source": "openHop Glass",
    "template_id": "<optional-template-id>"
  }
}
```

Notes:
- `policy` is the direct Repeater `policy_engine` object, not the full `policy.yaml` wrapper.
- The full `policy.yaml` document on Repeater should wrap it as:

```yaml
policy_engine:
  enabled: true
  default_action: allow
  rules: []
  objects: {}
groups:
  channel_hashes: []
  pubkeys: []
```

## Required Repeater changes

Implement `policy_sync` in `repeater/data_acquisition/glass_handler.py` inside `_execute_command_action`.

Suggested branch point:

```python
if action == "policy_sync":
    success, message, details = self._apply_policy_sync(params)
    return success, message, details
```

Add `_apply_policy_sync(params)` with this behavior:

1. Validate `params` is a dict.
2. Extract `policy = params.get("policy")` and require a dict.
3. Accept `mode` values:
   - `replace`: replace the existing `policy_engine` document with the Glass policy.
   - `patch`: merge fields into the existing `policy_engine` while preserving unspecified fields.
4. Accept `validate_only`:
   - If true, construct `PolicyEngine(policy)` and return success/failure without writing.
5. Resolve the existing policy file path using the same helpers Repeater `/api/policy` uses.
6. Load the existing full policy document if present.
7. Preserve existing `groups` from `policy.yaml` unless Glass sends a future explicit `groups` key.
8. Write back YAML as the full wrapper document:
   - `policy_engine: <normalized-policy>`
   - `groups: <existing-or-provided-groups>`
9. Apply runtime policy immediately:
   - `self.config["policy_engine"] = normalized_policy`
   - `self.config["policy_file_path"] = str(policy_path)`
   - `self.daemon_instance.repeater_handler.policy_engine = PolicyEngine.from_runtime_config(self.config)`
10. Return command details including:

```json
{
  "policy_file": "/etc/pymc_repeater/policy.yaml",
  "mode": "replace",
  "validate_only": false,
  "rule_count": 3,
  "enabled": true,
  "default_action": "allow"
}
```

## Reuse existing Repeater code

Prefer reusing/copying the logic already in Repeater dev `repeater/web/api_endpoints.py`:
- `_get_policy_file_path`
- `_load_policy_document`
- `_write_policy_document`
- `_normalize_policy_engine`
- `_sync_policy_engine_objects_from_groups`
- `_apply_policy_runtime`

Do not invent a second `policy.json` store. Use `policy.yaml`.

## Compatibility details

Glass policy editor now pre-stages object groups in:
- `objects.channel_hash_groups`
- `objects.pubkey_groups`

Repeater PolicyEngine currently also supports `objects.channel_hash_groups` for channel secret/hash matching. Keep field support aligned with `repeater/policy_engine.py`, including:
- `channel_hash`
- `channel_decryptable`
- `channel_message_body`
- `channel_sender`
- `payload_hex`
- `transport_code_0`
- `transport_code_1`

## Tests to add in Repeater

Add tests around `GlassHandler._execute_command_action("policy_sync", params)`:

1. `validate_only=true` validates but does not write `policy.yaml`.
2. `replace` writes a full wrapper document with `policy_engine` and preserved `groups`.
3. `patch` preserves existing `policy_engine.objects`/rules unless explicitly overwritten.
4. Runtime handler receives a new `PolicyEngine` instance.
5. Invalid policy returns `success=False` and a useful message.

## Acceptance check

After implementation, from Glass:
1. Create a Repeater Runtime Policy template.
2. Add a policy group and a rule that references it, e.g. `@channel_hash_groups.blocked_channels`.
3. Queue `policy_sync` to a dev Repeater.
4. Repeater reports command result `success` on next `/inform`.
5. Repeater `policy.yaml` contains the policy under `policy_engine` and preserved/merged `groups`.
