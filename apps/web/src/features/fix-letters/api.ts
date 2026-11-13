import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Fix-owner letters from readiness differences (FA-08, FR-DQ-049..060): letters to parents
 * asking for an Aadhaar update, the board registration correction letter, and "letters given".
 * The calls go through the BFF client, so CSRF, step-up (428) and sign-in handling apply.
 */

type Schemas = components["schemas"];

export type FixLetterKey = Schemas["FixLetterOut"]["key"];
export type FixLetterStudent = Schemas["FixLetterStudentOut"];
export type FixLetter = Schemas["FixLetterOut"];
export type FixLetters = Schemas["FixLettersOut"];
export type FixLetterSent = Schemas["FixLetterSentOut"];

export const FIX_LETTERS_MANAGE = "dq.readiness.manage";
export const FIX_LETTERS_BOARD_PRINT = "export.portal";
export const FIX_LETTER_PROFILES = ["bseap-ssc-2027", "apaar"] as const;

function base(profileKey: string): string {
  return `/api/v1/dq/readiness/${encodeURIComponent(profileKey)}`;
}

export const FIX_LETTER_KEYS = {
  all: ["staff", "dq", "fix-letters"] as const,
  overview: (profile: string) => ["staff", "dq", "fix-letters", profile] as const,
};

/** A parent letters print view (an API page with its own strict CSP) through the BFF. */
export function parentLettersHref(
  profileKey: string,
  target: { studentId: string } | { sectionId: string },
): string {
  const query =
    "studentId" in target
      ? `student_id=${encodeURIComponent(target.studentId)}`
      : `section_id=${encodeURIComponent(target.sectionId)}`;
  return `/bff${base(profileKey)}/parent-letters?${query}`;
}

/** The board letter print view through the BFF (export.portal, step-up). */
export function boardLetterHref(profileKey: string, recipient: string): string {
  return `/bff${base(profileKey)}/board-letter?recipient=${encodeURIComponent(recipient)}`;
}

export const fixLettersApi = {
  overview: (api: ApiClient, profileKey: string) =>
    unwrap(
      api.GET("/api/v1/dq/readiness/{profile_key}/fix-letters", {
        params: { path: { profile_key: profileKey } },
      }),
    ),
  /** Checks the board letter can be printed now (export.portal, step-up); 204, not audited. */
  checkBoardLetter: (api: ApiClient, profileKey: string, recipient: string) =>
    unwrap(
      api.GET("/api/v1/dq/readiness/{profile_key}/board-letter", {
        params: { path: { profile_key: profileKey }, query: { recipient, check: true } },
        parseAs: "text",
      }),
    ),
  /** Records the letter's follow-up step ("letters given" / "letter sent") for the students. */
  recordSent: (
    api: ApiClient,
    profileKey: string,
    key: FixLetterKey,
    studentIds: readonly string[],
    occurredOn: string,
    idempotencyKey: string,
  ) =>
    unwrap(
      api.POST("/api/v1/dq/readiness/{profile_key}/fix-letters/{letter_key}/sent", {
        params: { path: { profile_key: profileKey, letter_key: key } },
        body: { student_ids: [...studentIds], occurred_on: occurredOn },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ),
};

/** The letter's students grouped by section, in the order the API gave (class, then name). */
export function bySection(
  students: readonly FixLetterStudent[],
): { sectionId: string | null; label: string | null; students: FixLetterStudent[] }[] {
  const groups = new Map<
    string,
    { sectionId: string | null; label: string | null; students: FixLetterStudent[] }
  >();
  for (const row of students) {
    const key = row.section_id ?? "";
    const group = groups.get(key) ?? {
      sectionId: row.section_id,
      label: row.class_section,
      students: [],
    };
    group.students.push(row);
    groups.set(key, group);
  }
  return [...groups.values()];
}
