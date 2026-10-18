// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadAuthConfig } from "@/server/config";
import { testEnv } from "@/test/server-env";
import { documentRedirect, loadPayment, optinFromForm, payFromForm } from "./actions";
import { PUBLIC_TOKEN_HEADER, type PublicRuntime } from "./client";
import { parentCsrf } from "./csrf";

/**
 * The parent pages' PDF link, "Pay online" and language posts (wave R2-3H; ADR-0046 decision 9;
 * FR-PUB-009, FR-PUB-010, FR-PAY-005, FR-LANG-003). Synthetic tokens only.
 */

const TOKEN = "Abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE";
const PUBLIC = "https://my.school.example";
let calls: Request[];
let answer: (request: Request) => Response | Promise<Response>;

function runtime(): PublicRuntime {
  const config = loadAuthConfig(testEnv({ TRUSTED_PROXY_HOPS: "1" }));
  return {
    config,
    now: () => Date.parse("2026-10-18T10:00:00Z"),
    apiFetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      calls.push(request.clone());
      return answer(request);
    }) as typeof fetch,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function post(
  path: string,
  fields: [string, string][],
  headers: Record<string, string> = {},
): Request {
  const body = new URLSearchParams(fields).toString();
  return new Request(`${PUBLIC}${path}`, {
    method: "POST",
    body,
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      origin: PUBLIC,
      cookie: `__Host-sos_parent=${TOKEN}`,
      ...headers,
    },
  });
}

function csrf(rt: PublicRuntime): string {
  return parentCsrf(rt.config.serviceTokenKey, TOKEN);
}

beforeEach(() => {
  calls = [];
  answer = () =>
    json({ redirect_url: "https://rzp.io/rzp/AbCdEf", amount: "3500.00", reused: false });
  vi.stubEnv("PUBLIC_BASE_URL", PUBLIC);
  vi.stubEnv("APP_BASE_URL", "https://office.school.example");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("POST /p/pay (FR-PAY-005)", () => {
  it("sends the ticked instalments with the cookie's token and answers 303 to Razorpay", async () => {
    const rt = runtime();
    const res = await payFromForm(
      rt,
      post("/p/pay", [
        ["csrf", csrf(rt)],
        ["instalment", "2"],
        ["instalment", "1"],
        ["instalment", "2"],
        ["instalment", "x"],
      ]),
    );
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("https://rzp.io/rzp/AbCdEf");
    expect(res.headers.get("referrer-policy")).toBe("no-referrer");
    const [sent] = calls;
    expect(new URL(sent!.url).pathname).toBe("/api/v1/public/links/current/pay");
    expect(sent!.headers.get(PUBLIC_TOKEN_HEADER)).toBe(TOKEN);
    expect(await sent!.json()).toEqual({ instalments: [1, 2] });
  });

  it("refuses posts from another site, without the cookie or with a wrong CSRF token", async () => {
    const rt = runtime();
    const good = csrf(rt);
    for (const request of [
      post(
        "/p/pay",
        [
          ["csrf", good],
          ["instalment", "1"],
        ],
        { origin: "https://evil.example" },
      ),
      post(
        "/p/pay",
        [
          ["csrf", good],
          ["instalment", "1"],
        ],
        { cookie: "" },
      ),
      post("/p/pay", [
        ["csrf", "forged"],
        ["instalment", "1"],
      ]),
      post("/p/pay", [["instalment", "1"]]),
      post(
        "/p/pay",
        [
          ["csrf", good],
          ["instalment", "1"],
        ],
        { "content-type": "application/json" },
      ),
    ]) {
      const res = await payFromForm(rt, request);
      expect(res.status).toBe(403);
    }
    expect(calls).toHaveLength(0);
  });

  it("accepts the browser's no-referrer form post (Origin null, same-origin fetch)", async () => {
    const rt = runtime();
    const res = await payFromForm(
      rt,
      post(
        "/p/pay",
        [
          ["csrf", csrf(rt)],
          ["instalment", "1"],
        ],
        {
          origin: "null",
          "sec-fetch-site": "same-origin",
        },
      ),
    );
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("https://rzp.io/rzp/AbCdEf");
  });

  it("never sends the parent anywhere but Razorpay over https", async () => {
    const rt = runtime();
    for (const url of [
      "http://rzp.io/rzp/x",
      "https://rzp.io.evil.example/x",
      "https://evil.example/x",
      "javascript:alert(1)",
      "https://rzp.example.test/fake/x", // the fake provider: development only
    ]) {
      answer = () => json({ redirect_url: url, amount: "1.00", reused: false });
      const res = await payFromForm(
        rt,
        post("/p/pay", [
          ["csrf", csrf(rt)],
          ["instalment", "1"],
        ]),
      );
      expect(res.headers.get("location"), url).toBe("/p/view?pay=failed#pay");
    }
  });

  it("maps refusals to reason codes on the page", async () => {
    const rt = runtime();
    const cases: [Response, string][] = [
      [json({ code: "payments_not_connected" }, 409), "/p/view?pay=not_connected#pay"],
      [json({ code: "nothing_due" }, 409), "/p/view?pay=nothing_due#pay"],
      [json({ code: "too_many_links" }, 409), "/p/view?pay=too_many#pay"],
      [json({ code: "rate_limited" }, 429), "/p/view?pay=limit#pay"],
      [json({ code: "validation_error" }, 422), "/p/view?pay=choose#pay"],
      [json({ code: "payment_link_failed" }, 502), "/p/view?pay=failed#pay"],
      [json({ code: "not_found" }, 404), "/p/expired"],
    ];
    for (const [response, location] of cases) {
      answer = () => response;
      const res = await payFromForm(
        rt,
        post("/p/pay", [
          ["csrf", csrf(rt)],
          ["instalment", "1"],
        ]),
      );
      expect(res.headers.get("location")).toBe(location);
    }
    calls = [];
    const none = await payFromForm(rt, post("/p/pay", [["csrf", csrf(rt)]]));
    expect(none.headers.get("location")).toBe("/p/view?pay=choose#pay");
    expect(calls).toHaveLength(0);
  });
});

describe("POST /p/optin (FR-LANG-003)", () => {
  it("saves the language and comes back to the page", async () => {
    const rt = runtime();
    answer = () => json({ categories: [], language_choice: "te", school_language: "en" });
    const res = await optinFromForm(
      rt,
      post("/p/optin", [
        ["csrf", csrf(rt)],
        ["language", "te"],
      ]),
    );
    expect(res.headers.get("location")).toBe("/p/view?saved=1");
    expect(await calls[0]!.json()).toEqual({ language: "te" });
    const bad = await optinFromForm(
      rt,
      post("/p/optin", [
        ["csrf", csrf(rt)],
        ["language", "hi"],
      ]),
    );
    expect(bad.headers.get("location")).toBe("/p/view?saved=0");
    const forged = await optinFromForm(rt, post("/p/optin", [["language", "te"]]));
    expect(forged.status).toBe(403);
  });
});

describe("GET /p/document and the payment status", () => {
  it("redirects to the presigned PDF or back to the page", async () => {
    const rt = runtime();
    const request = new Request(`${PUBLIC}/p/document`, {
      headers: { cookie: `__Host-sos_parent=${TOKEN}` },
    });
    answer = () => json({ url: "https://files.example/x.pdf?sig=1", expires_at: "x" });
    expect((await documentRedirect(rt, request)).headers.get("location")).toBe(
      "https://files.example/x.pdf?sig=1",
    );
    answer = () => json({ code: "not_found" }, 404);
    expect((await documentRedirect(rt, request)).headers.get("location")).toBe("/p/view?pdf=0");
    answer = () => json({ url: "javascript:alert(1)", expires_at: "x" });
    expect((await documentRedirect(rt, request)).headers.get("location")).toBe("/p/view?pdf=0");
    const noCookie = new Request(`${PUBLIC}/p/document`);
    expect((await documentRedirect(rt, noCookie)).headers.get("location")).toBe("/p/expired");
  });

  it("asks for the named intent only when it is 32 hex characters", async () => {
    const rt = runtime();
    const headers = new Headers({ cookie: `__Host-sos_parent=${TOKEN}` });
    answer = () => json({ status: "paid", amount: "3500.00", receipt_issued: true });
    expect(await loadPayment(rt, headers, "a".repeat(32))).toEqual({
      state: "ok",
      payment: { status: "paid", amount: "3500.00", receipt_issued: true },
    });
    expect(new URL(calls[0]!.url).search).toBe(`?intent=${"a".repeat(32)}`);
    await loadPayment(rt, headers, "../../admin");
    expect(new URL(calls[1]!.url).search).toBe("");
    answer = () => json({ code: "not_found" }, 404);
    expect(await loadPayment(rt, headers, null)).toEqual({ state: "unknown" });
  });
});
