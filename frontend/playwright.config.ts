import { env } from "node:process";
import { defineConfig, devices } from "@playwright/test";

const suppliedURL = env.GLASS_E2E_BASE_URL;
if (!suppliedURL || env.GLASS_E2E_ISOLATED !== "1") {
  throw new Error("Set GLASS_E2E_BASE_URL and GLASS_E2E_ISOLATED=1 for an isolated test stack; no default target is permitted.");
}
const target = new URL(suppliedURL);
if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password || target.search || target.hash) {
  throw new Error("GLASS_E2E_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment.");
}

export default defineConfig({
  testDir: "./src/tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: target.href,
    trace: "off",
    screenshot: "off",
    video: "off",
    serviceWorkers: "block",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // No webServer: starting infrastructure is a separate, explicit operation.
});
