import type { RepeaterPolicyTemplateResponse, RepeaterResponse } from "../../types";

// Synthetic, sanitized documents shaped by the editor's EXAMPLE_POLICIES and
// types.ts opaque policy payload. Not captured RF telemetry or backend fixtures.
export const scalarPolicy = {
  enabled: true,
  default_action: "allow",
  rules: [{
    id: "baseline-rule", name: "Baseline", enabled: true,
    if: { all: [
      { field: "hop_count", op: "greater_than", value: 2 },
      { field: "local_transmission", op: "equals", value: false },
      { field: "sender_pubkey", op: "in", value: "@pubkey_groups.trusted_nodes" },
    ] },
    then: { action: "log_only" },
  }],
  objects: { channel_hash_groups: {}, pubkey_groups: { trusted_nodes: ["synthetic-node-key"] } },
};

export const arrayPolicy = {
  ...scalarPolicy,
  rules: [{
    ...scalarPolicy.rules[0],
    if: { all: [{ field: "path_hashes", op: "intersects", value: ["0x12", "0x34"] }] },
  }],
};

export const extensionPolicy = {
  ...scalarPolicy,
  extension_metadata: { origin: "sanitized-test", version: 1 },
  rules: [{ ...scalarPolicy.rules[0], extension_metadata: { preserve: true } }],
};

export function policyTemplate(policy: Record<string, unknown>): RepeaterPolicyTemplateResponse {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    name: "Sanitized baseline", description: null, enabled: true, policy,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  };
}

export const repeater: RepeaterResponse = {
  id: "00000000-0000-4000-8000-000000000002",
  node_name: "isolated-test-node", pubkey: "synthetic-node-key", status: "online",
  firmware_version: null, location: null, config_hash: null, inform_ip: null,
  open_url: null, last_inform_at: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};
