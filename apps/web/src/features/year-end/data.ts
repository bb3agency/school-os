"use client";

import type { ApiClient, components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * FT-08 year-end progression pack (FR-YRE-001..FR-YRE-020) through the BFF: a section's
 * proposed and confirmed annual results, confirming them (the principal), the held-back
 * suggestions for the promotion screen and the UDISE+ progression check sheet.
 */

export const YEAR_END_PERM = {
  read: "year_result.read",
  confirm: "year_result.confirm",
  portal: "export.portal",
} as const;

type S = components["schemas"];
export type YearResultCode = S["YearResultIn"]["result"];
export const YEAR_RESULTS: readonly YearResultCode[] = [
  "passed",
  "promoted",
  "detained",
  "not_appeared",
];
export type YearEndWarning = S["YearEndWarningOut"]["code"];
export type ProgressionBlank = S["ProgressionRowOut"]["blanks"][number];
export type YearEndRules = S["YearEndRulesOut"];
export type YearProposal = S["YearProposalOut"];
export type YearConfirmed = S["YearConfirmedOut"];
export type YearResultStudent = S["YearResultStudentOut"];
export type YearResults = S["YearResultsOut"];
export type YearResultEntry = S["YearResultIn"];
export type YearResultsWriteOut = S["YearResultsWriteOut"];
export type HeldBack = S["HeldBackOut"];
export type ProgressionRow = S["ProgressionRowOut"];
export type Progression = S["ProgressionOut"];

export const YEAR_END_KEYS = {
  all: ["staff", "year-end"] as const,
  section: (sectionId: string) => ["staff", "year-end", "section", sectionId] as const,
  heldBack: (yearId: string) => ["staff", "year-end", "held-back", yearId] as const,
  progression: (yearId: string) => ["staff", "year-end", "progression", yearId] as const,
};

export function fetchYearResults(api: ApiClient, sectionId: string): Promise<YearResults> {
  return unwrap(
    api.GET("/api/v1/sections/{section_id}/year-results", {
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
  return unwrap(
    api.POST("/api/v1/sections/{section_id}/year-results", {
      params: { path: { section_id: sectionId } },
      body: { entries: [...entries] },
    }),
  );
}

export function useHeldBack(yearId: string, enabled: boolean): Loadable<HeldBack> {
  const api = useBffClient("staff");
  return useApiQuery(
    YEAR_END_KEYS.heldBack(yearId),
    () =>
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}/year-results/held-back", {
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
      unwrap(
        api.GET("/api/v1/academic-years/{year_id}/udise-progression", {
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
