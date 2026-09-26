// @vitest-environment node
import { describe, expect, it } from "vitest";
import { nextFromReferer, safeNext } from "./redirect";

describe("safeNext (open-redirect prevention)", () => {
  it.each([
    "/en/settings/users",
    "/te/audit?actor=x&from=01%2F06%2F2026",
    "/en/settings/structure#years",
    "/",
  ])("keeps same-origin staff path %s", (path) => {
    expect(safeNext(path, "staff")).toBe(path);
  });

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
    expect(safeNext("/en/platform/tenants", "staff")).toBe("/");
    expect(safeNext("/en/settings/users", "operator")).toBe("/en/platform");
    expect(safeNext("/te/platform/schools?q=a", "operator")).toBe("/te/platform/schools?q=a");
    expect(safeNext("/en/platformx", "operator")).toBe("/en/platform");
  });

  it("accepts only same-origin referers", () => {
    const origin = "https://office.school.example";
    expect(nextFromReferer(`${origin}/en/settings/users?x=1`, origin, "staff")).toBe(
      "/en/settings/users?x=1",
    );
    expect(nextFromReferer("https://evil.example/en/settings", origin, "staff")).toBe("/");
    expect(nextFromReferer(null, origin, "operator")).toBe("/en/platform");
  });
});
