import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      // Next.js resolves `server-only` to an empty module on the server; the npm package's
      // default entry throws. Tests exercise server modules directly, so use the empty one.
      "server-only": path.resolve(import.meta.dirname, "src/test/server-only.ts"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
    css: false,
    // next-intl imports `next/navigation` without an extension; let Vite resolve it.
    server: { deps: { inline: ["next-intl"] } },
    restoreMocks: true,
  },
});
