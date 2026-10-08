import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

// Deliberately independent of vite.config.ts: no backend proxy or services.
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "jsdom",
    include: ["src/tests/**/*.test.ts"],
    setupFiles: ["src/tests/setup.ts"],
    clearMocks: true,
    restoreMocks: true,
  },
});
