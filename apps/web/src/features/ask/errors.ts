import { ApiError } from "@/lib/bff/query";

/**
 * Problem codes of the Ask conversation and memory endpoints (docs/09 Knowledge, ADR-0034) and
 * how the screens explain them. Each has `ask.errors.<code>.{title,body}` in en and te, which
 * `<ApiErrorAlert namespace="ask">` shows (errors.test.ts checks both languages).
 *
 * A refused title or memory text is a 422 `validation_error` whose field error carries the
 * reason (`errors[].code`); `askError` lifts that code so the alert says what to change.
 */

/** 422 on PATCH /knowledge/conversations/{id} (field `title`). */
export const TITLE_CODES = [
  "title_length",
  "title_personal_number",
  "title_control_characters",
] as const;

/** 422 on POST /knowledge/memories and PATCH /knowledge/memories/{id} (field `text`). */
export const MEMORY_REFUSALS = [
  "memory_personal_number",
  "memory_date",
  "memory_long_number",
  "memory_too_long",
  "memory_empty",
  "memory_seen_record",
  "memory_others",
  "memory_unsure",
] as const;

/** Problem codes (top-level `code`) of those endpoints and of regenerate / edit on ask. */
export const ASK_PROBLEM_CODES = [
  // 503: the memory check could not run, so nothing was stored.
  "memory_check_unavailable",
  // 409: memory is off (the member's or the school's switch), or it holds 30 items already.
  "memory_off",
  "memory_full",
  // 409 on POST /knowledge/ask with regenerate_of / edit_of.
  "message_superseded",
  "message_not_revisable",
  // 400: a change was sent without If-Match (the page always sends it).
  "if_match_required",
  // 412: changed in another window since the page read it.
  "precondition_failed",
] as const;

/** UI-only codes for a 404 where the generic "not found" would mislead. */
export const NOT_FOUND_CODES = ["conversation_not_found", "memory_not_found"] as const;
export type NotFoundCode = (typeof NOT_FOUND_CODES)[number];

/** Every code with its own message under `ask.errors`. */
export const ASK_ERROR_CODES = [
  ...TITLE_CODES,
  ...MEMORY_REFUSALS,
  ...ASK_PROBLEM_CODES,
  ...NOT_FOUND_CODES,
] as const;

const FIELD_CODES: ReadonlySet<string> = new Set([...TITLE_CODES, ...MEMORY_REFUSALS]);

/**
 * The error to show: a 422 with an Ask field code becomes an `ApiError` with that code (the
 * problem, and so its request id, is kept); a 404 becomes `notFound` when one is given (e.g. a
 * chat deleted in another window, a memory suggestion that expired after 24 hours). Anything
 * else is returned unchanged.
 */
export function askError(error: unknown, notFound?: NotFoundCode): unknown {
  if (!(error instanceof ApiError)) return error;
  if (error.status === 422) {
    const field = (error.problem.errors ?? []).find((item) => FIELD_CODES.has(item.code));
    if (field) return new ApiError(error.status, field.code, error.problem);
  }
  if (error.status === 404 && notFound) return new ApiError(404, notFound, error.problem);
  return error;
}

/** The code `askError` would show (for choosing between an explanation and "undone"). */
export function askErrorCode(error: unknown, notFound?: NotFoundCode): string | undefined {
  const shown = askError(error, notFound);
  return shown instanceof ApiError ? shown.code : undefined;
}

/** True when the page has its own explanation for this error (see ASK_ERROR_CODES). */
export function isExplained(error: unknown, notFound?: NotFoundCode): boolean {
  const code = askErrorCode(error, notFound);
  return code !== undefined && (ASK_ERROR_CODES as readonly string[]).includes(code);
}
