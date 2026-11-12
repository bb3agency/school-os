import type { ApiClient } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Fix-owner letters from readiness differences (FA-08, FR-DQ-049..060): letters to parents
 * asking for an Aadhaar update, the board registration correction letter, and "letters given".
 *
 * LOCALLY TYPED WRAPPER: these routes are new in wave F3 and not yet in the generated client
 * (`packages/api-client`). The types below mirror `app/dq/fix_letter_schemas.py`. After
 * `make openapi` the lead swaps them for `components["schemas"]["FixLetter…"]` and the typed
 * `api.GET/POST` calls. The calls still go through the BFF client, so CSRF, step-up (428) and
 * sign-in handling apply.
 */

export type FixLetterKey = "parent_aadhaar" | "board_registration";

export interface FixLetterStudent {
  student: { id: string; display_name: string | null; admission_no: string | null };
  section_id: string | null;
  class_section: string | null;
  fields: string[];
  last_sent_on: string | null;
}

export interface FixLetter {
  key: FixLetterKey;
  label: string;
  title: string;
  audience: "parent" | "office";
  fix_owner: "parent_aadhaar" | "school_udise";
  version: number;
  verified: boolean;
  followup_event: string;
  followup_channel: string;
  print_permission: string;
  step_up: boolean;
  recipients: { key: string; label: string }[];
  students_count: number;
  lines_count: number;
  students: FixLetterStudent[];
}

export interface FixLetters {
  profile_key: string;
  verified: boolean;
  letters: FixLetter[];
}

export interface FixLetterSent {
  recorded: number;
  fix_owner: string;
  event: string;
  channel: string;
}

type ClientResult = { data?: unknown; error?: unknown; response: Response };
type Init = {
  params?: { query?: Record<string, unknown> };
  body?: unknown;
  headers?: Record<string, string>;
  parseAs?: "json" | "text";
};
/** The generated client without its path typing (new routes only; see the note above). */
interface LooseClient {
  GET(path: string, init?: Init): Promise<ClientResult>;
  POST(path: string, init?: Init): Promise<ClientResult>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

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
    unwrap(loose(api).GET(`${base(profileKey)}/fix-letters`)) as Promise<FixLetters>,
  /** Checks the board letter can be printed now (export.portal, step-up); 204, not audited. */
  checkBoardLetter: (api: ApiClient, profileKey: string, recipient: string) =>
    unwrap(
      loose(api).GET(`${base(profileKey)}/board-letter`, {
        params: { query: { recipient, check: true } },
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
      loose(api).POST(`${base(profileKey)}/fix-letters/${key}/sent`, {
        body: { student_ids: studentIds, occurred_on: occurredOn },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    ) as Promise<FixLetterSent>,
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
