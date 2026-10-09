import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import AppLayout from "../components/layout/AppLayout.vue";
import { appState, initializeAppState, logoutAccount, refreshAllData } from "../state/appState";
import type { CommandQueueItemResponse } from "../types";
import { repeater } from "./fixtures/policies";

vi.mock("../api");
vi.mock("../components/layout/TopHeader.vue", () => ({ default: { template: "<header />" } }));
vi.mock("../components/layout/SidebarNav.vue", () => ({ default: { template: "<nav />" } }));

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

  it("renders successful repeaters even when the audit request fails", async () => {
    vi.mocked(api.listAudit).mockRejectedValue(new Error("Audit endpoint unavailable"));
    await refreshAllData();
    expect(appState.toastError).toBe("Audit endpoint unavailable");
    expect(appState.lastSyncAt).toBeNull();
    expect(appState.dataLoading).toBe(false);
    expect(api.listRepeaters).toHaveBeenCalledWith("synthetic-unit-token");
    expect(appState.repeaters).toEqual([repeater]);
  });

  it("renders repeaters before a slow commands request finishes", async () => {
    let resolveCommands!: (rows: CommandQueueItemResponse[]) => void;
    vi.mocked(api.listCommands).mockReturnValue(new Promise((resolve) => { resolveCommands = resolve; }));
    const refresh = refreshAllData();
    try {
      await flushPromises();
      expect(appState.dataLoading).toBe(true);
      expect(api.listRepeaters).toHaveBeenCalledWith("synthetic-unit-token");
      expect(appState.repeaters).toEqual([repeater]);
    } finally {
      // Always settle the production refresh, even when the red assertion fails.
      resolveCommands([]);
      await refresh;
    }
  });

  it("allows authenticated startup to render while an unrelated resource is still loading", async () => {
    localStorage.setItem("openhop_glass_token", "synthetic-unit-token");
    vi.mocked(api.getBootstrapStatus).mockResolvedValue({ needs_bootstrap: false, server_setup_complete: true });
    vi.mocked(api.getCurrentUser).mockResolvedValue(appState.user!);
    vi.stubGlobal("EventSource", class { close() {} addEventListener() {} });
    let resolveCommands!: (rows: CommandQueueItemResponse[]) => void;
    vi.mocked(api.listCommands).mockReturnValueOnce(new Promise((resolve) => { resolveCommands = resolve; }));
    let finished = false;
    const initialize = initializeAppState().then(() => { finished = true; });
    try {
      await flushPromises();
      expect(finished).toBe(true);
      expect(appState.repeaters).toEqual([repeater]);
      expect(appState.resources.repeaters.loading).toBe(false);
      expect(appState.resources.commands.loading).toBe(true);
    } finally {
      resolveCommands([]);
      await initialize;
      await flushPromises();
      await logoutAccount();
    }
  });

  it("does not report a complete sync when an endpoint rejects without an Error object", async () => {
    vi.mocked(api.listAudit).mockRejectedValueOnce(undefined);
    await refreshAllData();
    expect(appState.lastSyncAt).toBeNull();
    expect(appState.resources.audits.error).toBe("Request failed");
    expect(appState.toastError).toBe("Unexpected error");
  });

  it("preserves last good data on failure and clears the resource error after retry", async () => {
    await refreshAllData();
    const successfulAt = appState.resources.repeaters.lastSuccessAt;
    vi.mocked(api.listRepeaters).mockRejectedValueOnce(new Error("Fleet unavailable"));
    await refreshAllData();
    expect(appState.repeaters).toEqual([repeater]);
    expect(appState.resources.repeaters.lastSuccessAt).toBe(successfulAt);
    expect(appState.resources.repeaters.error).toBe("Fleet unavailable");
    await refreshAllData();
    expect(appState.resources.repeaters.error).toBeNull();
  });

  it("keeps a named resource warning visible after the transient toast expires", async () => {
    vi.mocked(api.listAudit).mockRejectedValue(new Error("Audit endpoint unavailable"));
    const wrapper = mount(AppLayout, { global: { stubs: { SidebarNav: true, TopHeader: true, RouterView: true } } });
    try {
      await flushPromises();
      vi.advanceTimersByTime(5000);
      await flushPromises();
      expect(appState.repeaters).toEqual([repeater]);
      expect(wrapper.find('[data-testid="resource-errors"]').exists()).toBe(true);
      expect(wrapper.find('[data-testid="resource-errors"]').text()).toContain("Audit");
      expect(appState.toastError).toBeNull();
    } finally {
      wrapper.unmount();
    }
  });

  it("does not restore fleet data when an old request finishes after logout", async () => {
    let resolveRepeaters!: (rows: typeof appState.repeaters) => void;
    vi.mocked(api.listRepeaters).mockReturnValueOnce(new Promise((resolve) => { resolveRepeaters = resolve; }));
    const refresh = refreshAllData();
    await flushPromises();
    await logoutAccount();
    resolveRepeaters([repeater]);
    await refresh;
    expect(appState.token).toBeNull();
    expect(appState.repeaters).toEqual([]);
    expect(appState.lastSyncAt).toBeNull();
  });

  it("keeps the newer fleet response when overlapping refreshes finish out of order", async () => {
    let resolveOlder!: (rows: typeof appState.repeaters) => void;
    vi.mocked(api.listRepeaters).mockReturnValueOnce(new Promise((resolve) => { resolveOlder = resolve; }));
    const older = refreshAllData();
    const latest = { ...repeater, node_name: "newer-fleet-response" };
    vi.mocked(api.listRepeaters).mockResolvedValueOnce([latest]);
    await refreshAllData();
    resolveOlder([repeater]);
    await older;
    expect(appState.repeaters).toEqual([latest]);
    expect(appState.dataLoading).toBe(false);
  });
});
