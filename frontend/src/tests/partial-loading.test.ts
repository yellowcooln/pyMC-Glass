import { env } from "node:process";
import { flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { appState, refreshAllData } from "../state/appState";
import type { CommandQueueItemResponse } from "../types";
import { repeater } from "./fixtures/policies";

vi.mock("../api");
// Only the known-defect output expectation changes in diagnostic red mode.
const diagnosticRed = env.GLASS_REGRESSION_RED === "1";

beforeEach(() => {
  vi.useFakeTimers();
  appState.token = "synthetic-unit-token";
  appState.user = { id: "unit-user", email: "operator@example.invalid", role: "operator", display_name: null };
  appState.repeaters = [];
  appState.pendingRepeaters = [];
  appState.commands = [];
  appState.audits = [];
  appState.users = [];
  appState.lastSyncAt = null;
  appState.dataLoading = false;
  appState.toastError = null;
  vi.mocked(api.listRepeaters).mockResolvedValue([repeater]);
  vi.mocked(api.listPendingAdoptions).mockResolvedValue([]);
  vi.mocked(api.listCommands).mockResolvedValue([]);
  vi.mocked(api.listAudit).mockResolvedValue([]);
  vi.mocked(api.listUsers).mockResolvedValue([]);
});
afterEach(() => {
  appState.token = null;
  appState.user = null;
  appState.repeaters = [];
  appState.pendingRepeaters = [];
  appState.commands = [];
  appState.audits = [];
  appState.users = [];
  appState.lastSyncAt = null;
  appState.dataLoading = false;
  appState.toastError = null;
  appState.toastSuccess = null;
  vi.clearAllTimers();
});

describe("production refreshAllData loading baseline", () => {
  it("makes no API calls without a session", async () => {
    appState.token = null;
    await refreshAllData();
    expect(api.listRepeaters).not.toHaveBeenCalled();
    expect(appState.dataLoading).toBe(false);
  });

  it("loads successful responses and does not request users for an operator", async () => {
    await refreshAllData();
    expect(appState.repeaters).toEqual([repeater]);
    expect(appState.lastSyncAt).not.toBeNull();
    expect(appState.dataLoading).toBe(false);
    expect(api.listUsers).not.toHaveBeenCalled();
    expect(api.listCommands).toHaveBeenCalledWith("synthetic-unit-token", { limit: 200 });
  });

  it("surfaces an endpoint error without reporting a successful sync", async () => {
    vi.mocked(api.listAudit).mockRejectedValue(new Error("Audit endpoint unavailable"));
    await refreshAllData();
    expect(appState.toastError).toBe("Audit endpoint unavailable");
    expect(appState.lastSyncAt).toBeNull();
    expect(appState.dataLoading).toBe(false);
  });

  it("KNOWN DEFECT CHARACTERIZATION: audit failure discards a successful repeater response", async () => {
    vi.mocked(api.listAudit).mockRejectedValue(new Error("Audit endpoint unavailable"));
    await refreshAllData();
    expect(appState.toastError).toBe("Audit endpoint unavailable");
    expect(appState.lastSyncAt).toBeNull();
    expect(appState.dataLoading).toBe(false);
    expect(api.listRepeaters).toHaveBeenCalledWith("synthetic-unit-token");
    expect(appState.repeaters).toEqual(diagnosticRed ? [repeater] : []);
  });

  it("KNOWN DEFECT CHARACTERIZATION: pending commands withhold successful repeaters", async () => {
    let resolveCommands!: (rows: CommandQueueItemResponse[]) => void;
    vi.mocked(api.listCommands).mockReturnValue(new Promise((resolve) => { resolveCommands = resolve; }));
    const refresh = refreshAllData();
    try {
      await flushPromises();
      expect(appState.dataLoading).toBe(true);
      expect(api.listRepeaters).toHaveBeenCalledWith("synthetic-unit-token");
      expect(appState.repeaters).toEqual(diagnosticRed ? [repeater] : []);
    } finally {
      // Always settle the production refresh, even when the red assertion fails.
      resolveCommands([]);
      await refresh;
    }
  });
});
