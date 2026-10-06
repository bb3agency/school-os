import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { describeApiError } from "@/lib/api-errors";
import { ApiError } from "@/lib/bff/query";
import { renderWithIntl } from "@/test/render";
import { ApiErrorAlert } from "./ApiErrorAlert";

/** 429 problems (P2-07, docs/09 §2.7): the UI says how long to wait, from `retry_after`. */
describe("ApiErrorAlert on 429", () => {
  it("tells the person how many seconds to wait", () => {
    const error = new ApiError(429, "rate_limited", {
      status: 429,
      code: "rate_limited",
      request_id: "req_synthetic429",
      retry_after: 17,
    } as never);
    renderWithIntl(<ApiErrorAlert error={error} />);
    expect(screen.getByText("Too many requests. Wait 17 seconds and try again.")).toBeTruthy();
  });

  it("falls back to the plain message without a usable retry_after", () => {
    const error = new ApiError(429, "rate_limited", { status: 429, retry_after: "soon" } as never);
    expect(describeApiError(error)).toMatchObject({ key: "rate_limited", status: 429 });
    expect(describeApiError(error)).not.toHaveProperty("retryAfter");
    renderWithIntl(<ApiErrorAlert error={error} />);
    expect(screen.getByText("Wait a minute, then try again.")).toBeTruthy();
  });
});

/** Owner decisions on the 2026-10-06 route audit: each refusal says what to do next. */
describe("ApiErrorAlert for the 2026-10-06 decisions", () => {
  it.each([
    ["rotation_pending", 409, "A key rotation is already in progress"],
    ["already_on_hold", 409, "This school is already on a security hold"],
  ])("%s has its own plain-language message", (code, status, title) => {
    const error = new ApiError(status, code, { status, code, request_id: "req_synth" } as never);
    expect(describeApiError(error)).toMatchObject({ key: code, status });
    renderWithIntl(<ApiErrorAlert error={error} />);
    expect(screen.getByText(title)).toBeTruthy();
  });
});
