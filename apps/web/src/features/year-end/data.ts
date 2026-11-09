"use client";

import type { ApiClient } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * FT-08 year-end progression pack (FR-YRE-001..FR-YRE-020) through the BFF: a section's
 * proposed and confirmed annual results, confirming them (the principal), the held-back
 * suggestions for the promotion screen and the UDISE+ progression check sheet.
 *
 * LEAD: these routes are not in the generated client until `make openapi` runs. Until then
 * they are called through a loosely typed view of the same BFF client with the hand-written
 * types below; after regeneration switch to `api.GET/POST` with `components["schemas"]`
 * (`YearResultsOut`, `YearResultsWrite`, `YearResultsWriteOut`, `HeldBackOut`,
 * `ProgressionOut`) and drop `loose()`.
 */

export const YEAR_END_PERM = {
  read: "year_result.read",
  confirm: "year_result.confirm",
  portal: "export.portal",
} as const;

export type YearResultCode = "passed" | "promoted" | "detained" | "not_appeared";
export const YEAR_RESULTS: readonly YearResultCode[] = [
  "passed",
  "promoted",
  "detained",
  "not_appeared",
];
export type YearEndWarning = "no_detention_class";
export type ProgressionBlank = "result" | "percent" | "days_attended" | "udise_pen";

export interface YearEndRules {
  rule_version: number;
  pass_percent: number;
  pass_percent_verified: boolean;
  no_detention_class_codes: string[];
  no_detention_verified: boolean;
}

export interface YearProposal {
  result: YearResultCode | null;
  percent: number | null;
  exams_counted: number;
  days_attended: number | null;
  working_days: number | null;
}

export interface YearConfirmed {
  result: YearResultCode;
  proposed_result: YearResultCode | null;
  reason: string | null;
  percent: number | null;
  days_attended: number | null;
  working_days: number | null;
  rule_version: number;
  confirmed_by_name: string | null;
  confirmed_at: string;
  version: number;
}

export interface YearResultStudent {
  student: {
    student_id: string;
    full_name: string | null;
    admission_no: string | null;
    roll_no: string | null;
  };
  proposal: YearProposal;
  confirmed: YearConfirmed | null;
  warnings: YearEndWarning[];
}

export interface YearResults {
  section: { id: string; class_id: string; academic_year_id: string; label: string };
  academic_year_label: string;
  no_detention_class: boolean;
  rules: YearEndRules;
  counts: {
    students: number;
    confirmed: number;
    unconfirmed: number;
    passed: number;
    promoted: number;
    detained: number;
    not_appeared: number;
  };
  students: YearResultStudent[];
}

export interface YearResultEntry {
  student_id: string;
  result: YearResultCode;
  reason?: string | null;
  expected_version?: number;
}

export interface YearResultsWriteOut {
  written: number;
  unchanged: number;
  warnings: Array<{ code: YearEndWarning; student_id: string }>;
}

export interface HeldBack {
  academic_year_id: string;
  held_back_student_ids: string[];
  no_detention_student_ids: string[];
  confirmed: number;
  unconfirmed: number;
  no_detention_verified: boolean;
}

export interface ProgressionRow {
  student_id: string;
  admission_no: string | null;
  full_name: string | null;
  udise_pen: string | null;
  class_code: string;
  section_label: string;
  result: YearResultCode | null;
  percent: number | null;
  days_attended: number | null;
  working_days: number | null;
  blanks: ProgressionBlank[];
}

export interface Progression {
  academic_year_id: string;
  academic_year_label: string;
  students: number;
  blank_counts: Record<string, number>;
  rows: ProgressionRow[];
}

export const YEAR_END_KEYS = {
  all: ["staff", "year-end"] as const,
  section: (sectionId: string) => ["staff", "year-end", "section", sectionId] as const,
  heldBack: (yearId: string) => ["staff", "year-end", "held-back", yearId] as const,
  progression: (yearId: string) => ["staff", "year-end", "progression", yearId] as const,
};

type LooseResult = { data?: unknown; error?: unknown; response: Response };
interface LooseClient {
  GET(path: string, init: { params: { path: Record<string, string> } }): Promise<LooseResult>;
  POST(
    path: string,
    init: { params: { path: Record<string, string> }; body?: unknown },
  ): Promise<LooseResult>;
}

function loose(api: ApiClient): LooseClient {
  return api as unknown as LooseClient;
}

async function read<T>(pending: Promise<LooseResult>): Promise<T> {
  return (await unwrap(pending as never)) as T;
}

export function fetchYearResults(api: ApiClient, sectionId: string): Promise<YearResults> {
  return read<YearResults>(
    loose(api).GET("/api/v1/sections/{section_id}/year-results", {
      params: { path: { section_id: sectionId } },
    }),
  );
}

export function useYearResults(sectionId: string, enabled: boolean): Loadable<YearResults> {
  const api = useBffClient("staff");
  return useApiQuery(YEAR_END_KEYS.section(sectionId), () => fetchYearResults(api, sectionId), {
    enabled: enabled && sectionId !== "",
  });
}

/** POST /sections/{id}/year-results (year_result.confirm; FR-YRE-004). */
export function confirmYearResults(
  api: ApiClient,
  sectionId: string,
  entries: readonly YearResultEntry[],
): Promise<YearResultsWriteOut> {
  return read<YearResultsWriteOut>(
    loose(api).POST("/api/v1/sections/{section_id}/year-results", {
      params: { path: { section_id: sectionId } },
      body: { entries },
    }),
  );
}

export function useHeldBack(yearId: string, enabled: boolean): Loadable<HeldBack> {
  const api = useBffClient("staff");
  return useApiQuery(
    YEAR_END_KEYS.heldBack(yearId),
    () =>
      read<HeldBack>(
        loose(api).GET("/api/v1/academic-years/{year_id}/year-results/held-back", {
          params: { path: { year_id: yearId } },
        }),
      ),
    { enabled: enabled && yearId !== "" },
  );
}

export function useProgression(yearId: string, enabled: boolean): Loadable<Progression> {
  const api = useBffClient("staff");
  return useApiQuery(
    YEAR_END_KEYS.progression(yearId),
    () =>
      read<Progression>(
        loose(api).GET("/api/v1/academic-years/{year_id}/udise-progression", {
          params: { path: { year_id: yearId } },
        }),
      ),
    { enabled: enabled && yearId !== "" },
  );
}

export function progressionCsvPath(yearId: string): string {
  return `/api/v1/academic-years/${encodeURIComponent(yearId)}/udise-progression/csv`;
}

/* ----------------------------------------------------------------------- the form (pure) */

export interface Choice {
  result: YearResultCode | "";
  reason: string;
}

/** The starting choice per student: the confirmed result, else the proposal, else nothing. */
export function initialChoices(rows: readonly YearResultStudent[]): Record<string, Choice> {
  const out: Record<string, Choice> = {};
  for (const row of rows) {
    out[row.student.student_id] = {
      result: row.confirmed?.result ?? row.proposal.result ?? "",
      reason: row.confirmed?.reason ?? "",
    };
  }
  return out;
}

export type ChoiceProblem = "reason_required" | "aadhaar";

const AADHAAR_LIKE = /(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)/;

/** What stops a choice from being sent (the API checks again). */
export function choiceProblem(choice: Choice): ChoiceProblem | null {
  const reason = choice.reason.trim();
  if (choice.result === "detained" && reason === "") return "reason_required";
  if (AADHAAR_LIKE.test(reason)) return "aadhaar";
  return null;
}

/**
 * The entries to send: students with a choice that differs from what is confirmed (new, or
 * a changed result or reason), each with the version read so a concurrent change is refused.
 */
export function changedEntries(
  rows: readonly YearResultStudent[],
  choices: Readonly<Record<string, Choice>>,
): YearResultEntry[] {
  const out: YearResultEntry[] = [];
  for (const row of rows) {
    const choice = choices[row.student.student_id];
    if (!choice || choice.result === "") continue;
    const reason = choice.reason.trim() === "" ? null : choice.reason.trim();
    const confirmed = row.confirmed;
    if (confirmed && confirmed.result === choice.result && (confirmed.reason ?? null) === reason) {
      continue;
    }
    out.push({
      student_id: row.student.student_id,
      result: choice.result,
      reason,
      expected_version: confirmed?.version ?? 0,
    });
  }
  return out;
}
