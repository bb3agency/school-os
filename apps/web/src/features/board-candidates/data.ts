import type { ApiClient } from "@schoolos/api-client";
import * as z from "@/lib/zod";
import { unwrap } from "@/lib/bff/query";

/**
 * Board candidate choices: medium, languages, subjects and the photo and signature tick
 * (FO-10, FR-STU-026..029). The office enters them per section; DQ-032 lists what is missing.
 * SchoolOS never holds the photograph or signature itself, only whether they were collected.
 *
 * The routes are not in the generated client yet (the lead regenerates apps/api/openapi.json
 * after the wave): the calls go through `loose()` and every answer is checked with the zod
 * schemas below. After regeneration, swap `loose(api)` for `api` and the zod types for
 * `components["schemas"]["BoardCandidateGridOut" | "BoardStudentCandidateOut" | ...]`.
 */

export const CANDIDATE_EDIT = "student.update_nonidentity";

export const CANDIDATE_KEYS = {
  all: ["staff", "board-candidates"] as const,
  section: (sectionId: string, scheme: string) =>
    ["staff", "board-candidates", "section", sectionId, scheme] as const,
  student: (studentId: string) => ["staff", "board-candidates", "student", studentId] as const,
};

const code = z.object({ code: z.string(), label: z.string() });

export const schemeSchema = z.object({
  key: z.string(),
  version: z.number(),
  label_en: z.string(),
  board: z.string(),
  classes: z.array(z.string()),
  verified: z.boolean(),
  source: z.array(z.string()),
  source_note: z.string(),
  medium_required: z.boolean(),
  medium_options: z.array(z.string()),
  languages: z.array(z.object({ slot: z.string(), required: z.boolean(), options: z.array(code) })),
  subjects: z.array(
    z.object({
      key: z.string(),
      label_en: z.string(),
      required: z.boolean(),
      min: z.number(),
      max: z.number(),
      options: z.array(code),
    }),
  ),
  max_subjects: z.number(),
  photo_required: z.boolean(),
  keys: z.array(z.string()),
});
export type BoardScheme = z.infer<typeof schemeSchema>;

export const ISSUE_REASONS = [
  "missing",
  "not_in_list",
  "repeated",
  "group_missing",
  "too_many",
  "photo",
] as const;
export type IssueReason = (typeof ISSUE_REASONS)[number];

const issueSchema = z.object({
  field: z.string(),
  reason: z.enum(ISSUE_REASONS),
  code: z.string().nullable().optional(),
  group: z.string().nullable().optional(),
});
export type CandidateIssue = z.infer<typeof issueSchema>;

const valuesSchema = z.record(z.string(), z.string().nullable());

export const gridSchema = z.object({
  section_id: z.string(),
  class_section: z.string().nullable(),
  class_code: z.string().nullable(),
  scheme: schemeSchema.nullable(),
  available_schemes: z.array(z.string()),
  rows: z.array(
    z.object({
      student_id: z.string(),
      admission_no: z.string().nullable(),
      display_name: z.string().nullable(),
      roll_no: z.string().nullable(),
      values: valuesSchema,
      issues: z.array(issueSchema),
      ready: z.boolean(),
    }),
  ),
  ready_count: z.number(),
});
export type CandidateGrid = z.infer<typeof gridSchema>;
export type CandidateRow = CandidateGrid["rows"][number];

export const studentCandidateSchema = z.object({
  student_id: z.string(),
  section_id: z.string().nullable(),
  class_section: z.string().nullable(),
  scheme: schemeSchema.nullable(),
  values: valuesSchema,
  issues: z.array(issueSchema),
  ready: z.boolean(),
});
export type StudentCandidate = z.infer<typeof studentCandidateSchema>;

const saveSchema = z.object({ recorded: z.number(), unchanged: z.number(), grid: gridSchema });
export type CandidateSaved = z.infer<typeof saveSchema>;

/** One student's changes; a field left out is not changed (values are never cleared). */
export interface CandidateEntry {
  student_id: string;
  exam_medium?: string;
  first_language?: string;
  second_language?: string;
  third_language?: string;
  board_subjects?: string[];
  photo_signature_ready?: boolean;
}

type Init = {
  params?: { path?: Record<string, string>; query?: Record<string, string> };
  body?: unknown;
};
type Result = Promise<{ data?: unknown; error?: unknown; response: Response }>;
interface LooseClient {
  GET(path: string, init?: Init): Result;
  PUT(path: string, init?: Init): Result;
}

/** The generated client without path typing, for the routes not generated yet (see above). */
function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

export async function fetchSectionGrid(
  api: ApiClient,
  sectionId: string,
  scheme: string,
): Promise<CandidateGrid> {
  const data = await unwrap(
    loose(api).GET("/api/v1/sections/{section_id}/board-candidates", {
      params: { path: { section_id: sectionId }, ...(scheme ? { query: { scheme } } : {}) },
    }),
  );
  return gridSchema.parse(data);
}

export async function saveSectionGrid(
  api: ApiClient,
  sectionId: string,
  input: { scheme: string; source: "manual_entry" | "parent_form"; rows: CandidateEntry[] },
): Promise<CandidateSaved> {
  const data = await unwrap(
    loose(api).PUT("/api/v1/sections/{section_id}/board-candidates", {
      params: { path: { section_id: sectionId } },
      body: input,
    }),
  );
  return saveSchema.parse(data);
}

export async function fetchStudentCandidate(
  api: ApiClient,
  studentId: string,
): Promise<StudentCandidate> {
  const data = await unwrap(
    loose(api).GET("/api/v1/students/{student_id}/board-candidate", {
      params: { path: { student_id: studentId } },
    }),
  );
  return studentCandidateSchema.parse(data);
}

// --- drafts ---------------------------------------------------------------------------------

/** What the grid edits for one student: single values plus one code per subject group. */
export interface Draft {
  exam_medium: string;
  first_language: string;
  second_language: string;
  third_language: string;
  /** Subject group key -> chosen code ("" = none). */
  subjects: Record<string, string>;
  photo: boolean;
}

const SINGLE_KEYS = ["exam_medium", "first_language", "second_language", "third_language"] as const;
export type SingleKey = (typeof SINGLE_KEYS)[number];
export const LANGUAGE_SLOTS = ["first_language", "second_language", "third_language"] as const;

/** The draft of a row as recorded now (subject codes sorted into the scheme's groups). */
export function draftOf(scheme: BoardScheme, values: Record<string, string | null>): Draft {
  const codes = (values.board_subjects ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  const subjects: Record<string, string> = {};
  for (const group of scheme.subjects) {
    subjects[group.key] = codes.find((c) => group.options.some((o) => o.code === c)) ?? "";
  }
  return {
    exam_medium: values.exam_medium ?? "",
    first_language: values.first_language ?? "",
    second_language: values.second_language ?? "",
    third_language: values.third_language ?? "",
    subjects,
    photo: values.photo_signature_ready === "yes",
  };
}

/** The subject codes of a draft in the scheme's group order. */
export function subjectCodes(scheme: BoardScheme, draft: Draft): string[] {
  return scheme.subjects.map((group) => draft.subjects[group.key] ?? "").filter(Boolean);
}

/** The changes between the recorded draft and the edited one, as an entry (null: none). */
export function entryFor(
  scheme: BoardScheme,
  studentId: string,
  before: Draft,
  after: Draft,
): CandidateEntry | null {
  const entry: CandidateEntry = { student_id: studentId };
  let changed = false;
  for (const key of SINGLE_KEYS) {
    if (after[key] && after[key] !== before[key]) {
      entry[key] = after[key];
      changed = true;
    }
  }
  const was = subjectCodes(scheme, before).join(",");
  const now = subjectCodes(scheme, after);
  if (scheme.subjects.length > 0 && now.length > 0 && now.join(",") !== was) {
    entry.board_subjects = now;
    changed = true;
  }
  if (after.photo !== before.photo) {
    entry.photo_signature_ready = after.photo;
    changed = true;
  }
  return changed ? entry : null;
}

/** Fill the blank single values of every draft with `fill` (the "same for the section" row). */
export function fillBlanks(
  drafts: Record<string, Draft>,
  fill: Partial<Record<SingleKey, string>>,
): Record<string, Draft> {
  const out: Record<string, Draft> = {};
  for (const [id, draft] of Object.entries(drafts)) {
    const next = { ...draft };
    for (const key of SINGLE_KEYS) {
      const value = fill[key];
      if (value && !next[key]) next[key] = value;
    }
    out[id] = next;
  }
  return out;
}
