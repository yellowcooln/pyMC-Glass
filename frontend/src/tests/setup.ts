import { afterEach, beforeEach, vi } from "vitest";

// Vitest aliases `window` to the Node global; use its exposed JSDOM instance
// to avoid Node's experimental storage before production modules import state.
const browserWindow = (globalThis as typeof globalThis & { jsdom: { window: Window } }).jsdom.window;
const browserStorage = browserWindow.localStorage;
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: browserStorage,
});

beforeEach(() => {
  browserStorage.clear();
  // A missing API mock must fail locally, never contact an implicit backend.
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("Unit tests forbid unmocked network requests"))));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
