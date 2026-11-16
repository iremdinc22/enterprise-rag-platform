import { defineConfig } from "vitest/config";
// Standalone development is intentionally UI-only. Only the isolated Compose
// frontend proxies API calls; never fall back to an existing localhost backend.
export default defineConfig({
  server: { host: "127.0.0.1" },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    restoreMocks: true,
  },
});
