// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { testEnv } from "@/test/server-env";
import { register } from "./instrumentation";

afterEach(() => {
  vi.unstubAllEnvs();
});

function stubEnv(env: Record<string, string | undefined>) {
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
}

describe("startup configuration check (SEC-006)", () => {
  it("refuses to start with a short SESSION_SECRET", async () => {
    stubEnv({ ...testEnv({ SESSION_SECRET: "too-short" }), NEXT_RUNTIME: "nodejs" });
    await expect(register()).rejects.toThrow("SESSION_SECRET must be at least 32 bytes");
  });

  it("starts with a valid configuration", async () => {
    stubEnv({ ...testEnv(), NEXT_RUNTIME: "nodejs" });
    await expect(register()).resolves.toBeUndefined();
  });

  it("does nothing in the edge runtime", async () => {
    stubEnv({ NEXT_RUNTIME: "edge", SESSION_SECRET: "" });
    await expect(register()).resolves.toBeUndefined();
  });
});
