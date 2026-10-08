import { env } from "node:process";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { appState } from "../state/appState";
import RepeaterPoliciesView from "../views/RepeaterPoliciesView.vue";
import { arrayPolicy, extensionPolicy, policyTemplate, scalarPolicy } from "./fixtures/policies";

vi.mock("../api");
// Switch only defect-specific expectations; setup, mounting and parsing always fail normally.
const diagnosticRed = env.GLASS_REGRESSION_RED === "1";
let wrapper: VueWrapper;

async function clickButton(label: string): Promise<void> {
  const button = wrapper.findAll("button").find((candidate) => candidate.text() === label);
  if (!button) throw new Error(`Missing button: ${label}`);
  await button.trigger("click");
}

async function loadJSON(policy: Record<string, unknown>): Promise<void> {
  await clickButton("JSON editor");
  await wrapper.get("textarea.policy-json").setValue(JSON.stringify(policy));
  // Control assertion: input receives the exact document before any conversion.
  expect(JSON.parse((wrapper.get("textarea.policy-json").element as HTMLTextAreaElement).value)).toEqual(policy);
}

async function roundTrip(policy: Record<string, unknown>): Promise<unknown> {
  await loadJSON(policy);
  await clickButton("Visual editor");
  await clickButton("JSON editor");
  return JSON.parse((wrapper.get("textarea.policy-json").element as HTMLTextAreaElement).value);
}

beforeEach(async () => {
  vi.useFakeTimers();
  appState.token = "synthetic-unit-token";
  appState.user = { id: "unit-user", email: "operator@example.invalid", role: "operator", display_name: null };
  vi.mocked(api.listRepeaterPolicyTemplates).mockResolvedValue([]);
  vi.mocked(api.listRepeaterPolicySyncStatus).mockResolvedValue([]);
  wrapper = mount(RepeaterPoliciesView);
  await flushPromises();
});
afterEach(() => {
  wrapper?.unmount();
  appState.token = null;
  appState.user = null;
  appState.toastError = null;
  appState.toastSuccess = null;
  vi.clearAllTimers();
});

describe("mounted production policy editor roundtrip", () => {
  it("preserves scalar types, rule order, group references and objects", async () => {
    expect(await roundTrip(scalarPolicy)).toEqual(scalarPolicy);
  });

  it("unwraps the supported policy_engine envelope", async () => {
    expect(await roundTrip({ policy_engine: scalarPolicy })).toEqual(scalarPolicy);
  });

  it("saves the exact advanced document in JSON mode", async () => {
    await loadJSON(extensionPolicy);
    vi.mocked(api.createRepeaterPolicyTemplate).mockResolvedValue(policyTemplate(extensionPolicy));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(api.createRepeaterPolicyTemplate).toHaveBeenCalledWith("synthetic-unit-token", expect.objectContaining({ policy: extensionPolicy }));
  });

  it("KNOWN DEFECT CHARACTERIZATION: visual roundtrip converts literal arrays to strings", async () => {
    const actual = await roundTrip(arrayPolicy);
    const knownIncorrect = {
      ...arrayPolicy,
      rules: [{
        ...arrayPolicy.rules[0],
        if: { all: [{ field: "path_hashes", op: "intersects", value: '["0x12","0x34"]' }] },
      }],
    };
    // A fix must fail this baseline and prompt promotion to a correctness test.
    expect(actual).toEqual(diagnosticRed ? arrayPolicy : knownIncorrect);
  });

  it("KNOWN DEFECT CHARACTERIZATION: visual roundtrip drops extension fields", async () => {
    const actual = await roundTrip(extensionPolicy);
    expect(actual).toEqual(diagnosticRed ? extensionPolicy : scalarPolicy);
  });
});
