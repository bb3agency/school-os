// @vitest-environment node
import { describe, expect, it } from "vitest";
import { nextFromReferer, safeNext } from "./redirect";

describe("safeNext (open-redirect prevention)", () => {
  it.each([
    "/settings/users",
    "/audit?actor=x&from=01%2F06%2F2026",
    "/settings/structure#years",
    "/ask/c/0192f3a4-0000-7000-8000-00000000c001",
    "/",
  ])("keeps same-origin staff path %s", (path) => {
    expect(safeNext(path, "staff")).toBe(path);
  });

  it("drops an old locale prefix, so no return address carries a locale (ADR-0036 note)", () => {
    expect(safeNext("/en/settings/users", "staff")).toBe("/settings/users");
    expect(safeNext("/te/audit?actor=x&from=01%2F06%2F2026", "staff")).toBe(
      "/audit?actor=x&from=01%2F06%2F2026",
    );
    expect(safeNext("/en/settings/structure#years", "staff")).toBe("/settings/structure#years");
    expect(safeNext("/en", "staff")).toBe("/");
    expect(safeNext("/te/platform/schools?q=a", "operator")).toBe("/platform/schools?q=a");
  });

  it.each(["/en//evil.example", "/te//evil.example/x", "/en/..//evil.example", "/en/%2F/evil"])(
    "never turns an old prefix into a protocol-relative address: %s",
    (value) => {
      const result = safeNext(value, "staff");
      expect(result.startsWith("/")).toBe(true);
      expect(result.startsWith("//")).toBe(false);
      expect(new URL(result, "https://office.school.example").host).toBe("office.school.example");
    },
  );

  it.each([
    "https://evil.example/",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/.//evil.example",
    "/./%2e/../..//evil.example",
    "/%0d%0aSet-Cookie:x=y",
    "/\t/evil.example",
    "/ /evil.example",
    "javascript:alert(1)",
    "data:text/html,hi",
    "en/settings",
    "",
    "/bff/auth/login",
    "/bff/api/v1/users",
    `/${"a".repeat(3000)}`,
  ])("rejects %s", (value) => {
    const result = safeNext(value, "staff");
    expect(result === "/" || result.startsWith("/%0")).toBe(true);
    expect(result.startsWith("//")).toBe(false);
  });

  it("encodes CR/LF instead of passing them through", () => {
    expect(safeNext("/%0d%0aSet-Cookie:x=y", "staff")).toBe("/%0d%0aSet-Cookie:x=y");
    expect(safeNext("/\r\nSet-Cookie:x=y", "staff")).toBe("/");
  });

  it("keeps staff out of the control plane and operators inside it", () => {
    expect(safeNext("/platform/tenants", "staff")).toBe("/");
    expect(safeNext("/platform", "support")).toBe("/");
    expect(safeNext("/en/platform/tenants", "staff")).toBe("/");
    expect(safeNext("/settings/users", "operator")).toBe("/platform");
    expect(safeNext("/platform/schools?q=a", "operator")).toBe("/platform/schools?q=a");
    expect(safeNext("/platformx", "operator")).toBe("/platform");
  });

  it("accepts only same-origin referers", () => {
    const origin = "https://office.school.example";
    expect(nextFromReferer(`${origin}/settings/users?x=1`, origin, "staff")).toBe(
      "/settings/users?x=1",
    );
    expect(nextFromReferer("https://evil.example/settings", origin, "staff")).toBe("/");
    expect(nextFromReferer(null, origin, "operator")).toBe("/platform");
  });
});
