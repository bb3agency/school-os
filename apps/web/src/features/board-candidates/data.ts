import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap } from "@/lib/bff/query";

/**
 * Board candidate choices: medium, languages, subjects and the photo and signature tick
 * (FO-10, FR-STU-026..029). The office enters them per section; DQ-032 lists what is missing.
 * SchoolOS never holds the photograph or signature itself, only whether they were collected.
 */

export const CANDIDATE_EDIT = "student.update_nonidentity";

export const CANDIDATE_KEYS = {
  all: ["staff", "board-candidates"] as const,
  section: (sectionId: string, scheme: string) =>
    ["staff", "board-candidates", "section", sectionId, scheme] as const,
  student: (studentId: string) => ["staff", "board-candidates", "student", studentId] as const,
};

type S = components["schemas"];

export type BoardScheme = S["BoardSchemeOut"];

export const ISSUE_REASONS = [
  "missing",
  "not_in_list",
  "repeated",
  "group_missing",
  "too_many",
  "photo",
] as const satisfies readonly S["BoardCandidateIssueOut"]["reason"][];
export type IssueReason = S["BoardCandidateIssueOut"]["reason"];
export type CandidateIssue = S["BoardCandidateIssueOut"];
export type CandidateGrid = S["BoardCandidateGridOut"];
export type CandidateRow = CandidateGrid["rows"][number];
export type StudentCandidate = S["BoardStudentCandidateOut"];
export type CandidateSaved = S["BoardCandidateSaveOut"];

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

export async function fetchSectionGrid(
  api: ApiClient,
  sectionId: string,
  scheme: string,
): Promise<CandidateGrid> {
  return unwrap(
    api.GET("/api/v1/sections/{section_id}/board-candidates", {
      params: { path: { section_id: sectionId }, ...(scheme ? { query: { scheme } } : {}) },
    }),
  );
}

export async function saveSectionGrid(
  api: ApiClient,
  sectionId: string,
  input: { scheme: string; source: "manual_entry" | "parent_form"; rows: CandidateEntry[] },
): Promise<CandidateSaved> {
  return unwrap(
    api.PUT("/api/v1/sections/{section_id}/board-candidates", {
      params: { path: { section_id: sectionId } },
      body: input,
    }),
  );
}

export async function fetchStudentCandidate(
  api: ApiClient,
  studentId: string,
): Promise<StudentCandidate> {
  return unwrap(
    api.GET("/api/v1/students/{student_id}/board-candidate", {
      params: { path: { student_id: studentId } },
    }),
  );
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
