// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { testEnv } from "@/test/server-env";

/**
 * The parent, disclosure and verification pages never need Valkey (WS-04, FR-PUB-001): their
 * runtime is the configuration, the service-token key and fetch. With Valkey unreachable they
 * keep working instead of failing with Next.js's generic 500 page. Synthetic tokens only.
 */

const connectRedis = vi.hoisted(() => vi.fn());
vi.mock("@/server/session/redis", () => ({ connectRedis }));

const TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE";
const KEY = "AbCdEfGhIjKlMnOpQrStUv";
const PRESIGNED = "https://files.school.example/t/x/notice.pdf?X-Amz-Signature=synthetic";
let apiCalls: string[];

beforeEach(async () => {
  for (const [name, value] of Object.entries(
    testEnv({ PUBLIC_BASE_URL: "https://my.school.example" }),
  )) {
    if (value !== undefined) vi.stubEnv(name, value);
  }
  connectRedis.mockReset();
  connectRedis.mockRejectedValue(new Error("connect ECONNREFUSED valkey.test:6379"));
  apiCalls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input instanceof Request ? input.url : input.toString());
      apiCalls.push(url.pathname);
      return Response.json({ url: PRESIGNED });
    }),
  );
  const { setPublicRuntimeForTesting } = await import("./runtime");
  setPublicRuntimeForTesting(null);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("public runtime without Valkey (WS-04, FR-PUB-001)", () => {
  it("is built from the configuration only and never connects to Valkey", async () => {
    const { getPublicRuntime } = await import("./runtime");
    const runtime = await getPublicRuntime();
    expect(runtime.config.apiInternalUrl.href).toBe("http://api.internal:8000/");
    expect(connectRedis).not.toHaveBeenCalled();
  });

  it("GET /p/document still answers 303 to the file while Valkey is down", async () => {
    const { GET } = await import("@/app/(public)/p/document/route");
    const response = await GET(
      new Request("https://my.school.example/p/document", {
        headers: { cookie: `__Host-sos_parent=${TOKEN}` },
      }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(PRESIGNED);
    expect(apiCalls).toEqual(["/api/v1/public/links/current/document"]);
    expect(connectRedis).not.toHaveBeenCalled();
  });

  it("POST /p/pay and /p/optin still answer (403 without the parent cookie) while Valkey is down", async () => {
    const { POST: pay } = await import("@/app/(public)/p/pay/route");
    const { POST: optin } = await import("@/app/(public)/p/optin/route");
    for (const handler of [pay, optin]) {
      const response = await handler(
        new Request("https://my.school.example/p/pay", { method: "POST" }),
      );
      expect(response.status).toBe(403);
    }
    expect(connectRedis).not.toHaveBeenCalled();
  });

  it("GET /d/{key}/documents/{n} still answers 303 to the file while Valkey is down", async () => {
    const { GET } = await import("@/app/(public)/d/[key]/documents/[n]/route");
    const response = await GET(new Request(`https://my.school.example/d/${KEY}/documents/1`), {
      params: Promise.resolve({ key: KEY, n: "1" }),
    });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(PRESIGNED);
    expect(connectRedis).not.toHaveBeenCalled();
  });

  it("GET /p/{token} still exchanges the link while Valkey is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          kind: "parent_notice",
          expires_at: new Date(Date.now() + 3600_000).toISOString(),
        }),
      ),
    );
    const { GET } = await import("@/app/(public)/p/[token]/route");
    const response = await GET(new Request(`https://my.school.example/p/${TOKEN}`), {
      params: Promise.resolve({ token: TOKEN }),
    });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/p/view");
    expect(connectRedis).not.toHaveBeenCalled();
  });

  it("no page or route of the public group uses the Valkey-backed runtime", () => {
    const root = path.join(process.cwd(), "src", "app", "(public)");
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.")) files.push(full);
      }
    };
    walk(root);
    expect(files.length).toBeGreaterThan(5);
    const offenders = files.filter((file) => readFileSync(file, "utf8").includes("getAuthRuntime"));
    expect(offenders).toEqual([]);
  });
});
