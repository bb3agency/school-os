import { describe, expect, it } from "vitest";
import en from "../../../messages/en.json";
import te from "../../../messages/te.json";
import { ApiError } from "@/lib/bff/query";
import { ASK_ERROR_CODES, askError, askErrorCode, isExplained } from "./errors";

type Messages = { title?: unknown; body?: unknown };
const TELUGU = /[ఀ-౿]/;

describe("Ask error codes (docs/09 Knowledge, FR-KB-012, NFR-I18N-001)", () => {
  it("every conversation and memory code has a plain-language title and body in en and te", () => {
    const enErrors = en.ask.errors as Record<string, Messages>;
    const teErrors = te.ask.errors as Record<string, Messages>;
    for (const code of ASK_ERROR_CODES) {
      for (const [locale, errors] of [
        ["en", enErrors],
        ["te", teErrors],
      ] as const) {
        const message = errors[code];
        expect(typeof message?.title, `${locale}:${code}.title`).toBe("string");
        expect(typeof message?.body, `${locale}:${code}.body`).toBe("string");
      }
      expect(String(teErrors[code]?.body), `te:${code} is Telugu`).toMatch(TELUGU);
    }
    // The temporary contract's single code is gone: the API never sends it.
    expect(enErrors).not.toHaveProperty("memory_not_allowed");
  });

  it("lifts the reason of a refused title or memory from the 422 field error", () => {
    const problem = {
      code: "validation_error",
      request_id: "req_1",
      errors: [{ field: "text", code: "memory_date", message_key: "errors.memory_date" }],
    };
    const shown = askError(new ApiError(422, "validation_error", problem));
    expect(shown).toBeInstanceOf(ApiError);
    expect((shown as ApiError).code).toBe("memory_date");
    expect((shown as ApiError).problem.request_id).toBe("req_1");
    expect(isExplained(new ApiError(422, "validation_error", problem))).toBe(true);

    // Another field error (e.g. the question length) stays a validation error.
    const other = new ApiError(422, "validation_error", {
      errors: [{ field: "question", code: "too_long", message_key: "errors.too_long" }],
    });
    expect(askError(other)).toBe(other);
    expect(isExplained(other)).toBe(false);
  });

  it("names a 404 by what was missing, and keeps problem codes as sent", () => {
    const missing = new ApiError(404, "not_found");
    expect(askErrorCode(missing, "memory_not_found")).toBe("memory_not_found");
    expect(askErrorCode(missing)).toBe("not_found");
    expect(askErrorCode(new ApiError(409, "memory_full"))).toBe("memory_full");
    expect(isExplained(new ApiError(503, "memory_check_unavailable"))).toBe(true);
    expect(isExplained(new ApiError(500, "internal_error"))).toBe(false);
    expect(askError("offline")).toBe("offline");
  });
});
