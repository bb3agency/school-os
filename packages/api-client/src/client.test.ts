import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createApiClient, type FetchLike } from "./index";

const here = dirname(fileURLToPath(import.meta.url));

function recordingFetch(body: unknown, status = 200) {
  const calls: Request[] = [];
  const fetchImpl: FetchLike = async (request) => {
    calls.push(request);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return { calls, fetchImpl };
}

describe("createApiClient", () => {
  it("prefixes the base URL and fills path parameters", async () => {
    const { calls, fetchImpl } = recordingFetch({ id: "t-1" });
    const client = createApiClient("https://bff.example/bff/", fetchImpl);

    await client.GET("/api/v1/platform/tenants/{tenant_id}", {
      params: { path: { tenant_id: "0192f3a4-0000-7000-8000-000000000001" } },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(
      "https://bff.example/bff/api/v1/platform/tenants/0192f3a4-0000-7000-8000-000000000001",
    );
    expect(calls[0]?.headers.get("accept")).toBe("application/json");
  });

  it("serialises query parameters and returns typed data", async () => {
    const { calls, fetchImpl } = recordingFetch({ data: [], next_cursor: null });
    const client = createApiClient("http://api.internal:8000", fetchImpl);

    const { data, error } = await client.GET("/api/v1/platform/subscriptions", {
      params: { query: { status: "past_due", limit: 50 } },
    });

    expect(error).toBeUndefined();
    expect(data?.next_cursor).toBeNull();
    expect(new URL(calls[0]?.url ?? "").searchParams.get("status")).toBe("past_due");
  });

  it("surfaces problem+json errors", async () => {
    const problem = { type: "about:blank", title: "Not found", status: 404, code: "not_found" };
    const { fetchImpl } = recordingFetch(problem, 404);
    const client = createApiClient("http://api.internal:8000", fetchImpl);

    const { data, error } = await client.GET("/api/v1/platform/dashboard");

    expect(data).toBeUndefined();
    expect(error).toMatchObject({ code: "not_found" });
  });

  it("sends typed bodies and headers for control-plane writes", async () => {
    const { calls, fetchImpl } = recordingFetch({}, 201);
    const client = createApiClient("http://api.internal:8000", fetchImpl);

    await client.POST("/api/v1/platform/tenants/{tenant_id}/offboarding", {
      params: { path: { tenant_id: "0192f3a4-0000-7000-8000-000000000001" } },
      body: { reason: "School asked in writing to leave" },
    });
    await client.POST("/api/v1/platform/plans", {
      params: { header: { "Idempotency-Key": "plan-standard-v2" } },
      body: {
        code: "standard",
        name: "Standard",
        tier: "shared",
        billing_period: "monthly",
        pricing_model: "flat",
        base_price_inr: "4999.00",
        gst_rate: "18",
        trial_days: 30,
      },
    });

    expect(calls[0]?.url).toBe(
      "http://api.internal:8000/api/v1/platform/tenants/0192f3a4-0000-7000-8000-000000000001/offboarding",
    );
    expect(await calls[0]?.json()).toEqual({ reason: "School asked in writing to leave" });
    expect(calls[1]?.headers.get("idempotency-key")).toBe("plan-standard-v2");
  });
});

describe("generated types", () => {
  it("match apps/api/openapi.json (run `npm run generate -w @schoolos/api-client`)", async () => {
    const { default: openapiTS, astToString } = await import("openapi-typescript");
    const spec = resolve(here, "../../../apps/api/openapi.json");
    const ast = await openapiTS(new URL(`file://${spec}`), {
      alphabetize: true,
      exportType: false,
      immutable: false,
    });
    const committed = readFileSync(resolve(here, "generated/schema.ts"), "utf8");
    expect(committed.endsWith(astToString(ast))).toBe(true);
  });

  it("cover every path of the API, and nothing hand-written", () => {
    const spec = JSON.parse(
      readFileSync(resolve(here, "../../../apps/api/openapi.json"), "utf8"),
    ) as { paths: Record<string, unknown> };
    const committed = readFileSync(resolve(here, "generated/schema.ts"), "utf8");
    for (const path of Object.keys(spec.paths)) {
      expect(committed).toContain(`"${path}": {`);
    }
    const index = readFileSync(resolve(here, "index.ts"), "utf8");
    expect(index).toContain('from "./generated/schema"');
  });
});

describe("generate script", () => {
  it("fails with a clear message when the OpenAPI document is missing", () => {
    const script = resolve(here, "../scripts/generate.mjs");
    const result = spawnSync(process.execPath, [script], {
      env: { ...process.env, OPENAPI_SPEC: resolve(here, "does-not-exist/openapi.json") },
      encoding: "utf8",
    });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("OpenAPI document not found");
    expect(result.stderr).toContain("OPENAPI_SPEC");
  });
});
