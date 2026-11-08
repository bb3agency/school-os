import { describe, expect, it } from "vitest";
import { AuthRedirectError } from "./fetch";
import { ApiError, NotAvailableError, toLoadable } from "./query";
import { StepUpCancelledError } from "./step-up";

const ROWS = [{ id: "a" }];

function failed(error: unknown, data: typeof ROWS | null = ROWS) {
  return toLoadable({ isPending: false, isError: true, error, data: data ?? undefined });
}

describe("toLoadable after a failed background refetch (XO-07)", () => {
  it.each([
    ["a 5xx", new ApiError(502, "bad_gateway")],
    ["a timeout", new ApiError(504, undefined)],
    ["rate limiting", new ApiError(429, "rate_limited")],
    ["an offline network", new TypeError("Failed to fetch")],
  ])("keeps the data shown after %s and marks it refreshFailed", (_name, error) => {
    expect(failed(error)).toEqual({ status: "ready", data: ROWS, refreshFailed: true });
  });

  it("a good answer has no refreshFailed flag", () => {
    expect(toLoadable({ isPending: false, isError: false, error: null, data: ROWS })).toEqual({
      status: "ready",
      data: ROWS,
    });
  });

  it.each([
    [
      "no access any more (403)",
      new ApiError(403, "forbidden"),
      { status: "error", reason: "forbidden" },
    ],
    [
      "the school is suspended",
      new ApiError(403, "tenant_suspended"),
      { status: "error", reason: "tenant_suspended" },
    ],
    ["MFA needed", new ApiError(403, "mfa_required"), { status: "error", reason: "mfa_required" }],
    [
      "the record is gone (404)",
      new ApiError(404, "not_found"),
      { status: "error", reason: "not_found" },
    ],
    ["signed out (401)", new ApiError(401, "unauthenticated"), { status: "error" }],
    ["a cancelled step-up", new StepUpCancelledError(), { status: "error" }],
    [
      "sign-in or step-up navigation",
      new AuthRedirectError("/bff/auth/login"),
      { status: "loading" },
    ],
    ["a route this deployment lacks", new NotAvailableError(), { status: "unavailable" }],
  ])("still replaces the data when %s", (_name, error, expected) => {
    expect(failed(error)).toEqual(expected);
  });

  it("without data an error is still the error state", () => {
    expect(failed(new ApiError(502, "bad_gateway"), null)).toEqual({ status: "error" });
    expect(failed(new TypeError("Failed to fetch"), null)).toEqual({ status: "error" });
  });
});
