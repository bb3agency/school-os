"use client";

import type { components } from "@schoolos/api-client";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { Loadable } from "@/lib/loadable";

/**
 * CBSE Appendix IX disclosure (R3-1C; ADR-0051; US-2901..US-2904; FR-DISC-001..012).
 * `disclosure.edit` (owner, principal, office admin) edits the draft and downloads files;
 * `disclosure.publish` (owner, principal; step-up MFA) publishes, withdraws and changes the link.
 * The API checks every permission; these values only decide what the screen offers.
 */

export const EDIT = "disclosure.edit";
export const PUBLISH = "disclosure.publish";

export type DisclosureFormat = components["schemas"]["DisclosureFormatOut"];
export type FormatItem = components["schemas"]["FormatItemOut"];
export type FormatSection = components["schemas"]["FormatSectionOut"];
export type DisclosureDraft = components["schemas"]["DisclosureDraftOut"];
export type DerivedValue = components["schemas"]["DerivedValueOut"];
export type DraftDocument = components["schemas"]["DraftDocumentOut"];
export type Snapshot = components["schemas"]["SnapshotOut"];
export type SnapshotDetail = components["schemas"]["SnapshotDetailOut"];
export type DisclosureLink = components["schemas"]["LinkOut"];
export type TableValue = components["schemas"]["TableOut"];

export const DISCLOSURE_KEYS = {
  all: ["staff", "disclosure"] as const,
  format: ["staff", "disclosure", "format"] as const,
  draft: ["staff", "disclosure", "draft"] as const,
  snapshots: ["staff", "disclosure", "snapshots"] as const,
  link: ["staff", "disclosure", "link"] as const,
} as const;

export const RESULT_CLASSES = ["X", "XII"] as const;
export type ResultClass = (typeof RESULT_CLASSES)[number];

/** One board-result row as the form keeps it (strings until sent). */
export interface ResultRow {
  year: string;
  class_level: ResultClass;
  appeared: string;
  passed: string;
}

/** The editor's state: text for every typed item, result rows, chosen document ids. */
export interface DraftState {
  values: Record<string, string>;
  results: Record<string, ResultRow[]>;
  documents: Record<string, string[]>;
}

export function useDisclosureFormat(enabled: boolean): Loadable<DisclosureFormat> {
  const api = useBffClient("staff");
  return useApiQuery(DISCLOSURE_KEYS.format, () => unwrap(api.GET("/api/v1/disclosure/format")), {
    enabled,
  });
}

export function useDisclosureDraft(enabled: boolean): Loadable<DisclosureDraft> {
  const api = useBffClient("staff");
  return useApiQuery(DISCLOSURE_KEYS.draft, () => unwrap(api.GET("/api/v1/disclosure/draft")), {
    enabled,
  });
}

export function useDisclosureSnapshots(enabled: boolean): Loadable<Snapshot[]> {
  const api = useBffClient("staff");
  return useApiQuery(
    DISCLOSURE_KEYS.snapshots,
    () => unwrap(api.GET("/api/v1/disclosure/snapshots")),
    { enabled },
  );
}

export function useDisclosureLink(enabled: boolean): Loadable<DisclosureLink> {
  const api = useBffClient("staff");
  return useApiQuery(DISCLOSURE_KEYS.link, () => unwrap(api.GET("/api/v1/disclosure/link")), {
    enabled,
  });
}

/** `W/"3"` for If-Match (0 before the first save). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

function asText(value: unknown): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return String(value);
  return typeof value === "string" ? value : "";
}

function asRows(value: unknown): ResultRow[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw): ResultRow[] => {
    const row = raw as Partial<Record<string, unknown>>;
    const level = row.class_level === "XII" ? "XII" : "X";
    return [
      {
        year: asText(row.year),
        class_level: level,
        appeared: asText(row.appeared),
        passed: asText(row.passed),
      },
    ];
  });
}

/** The editor's starting state from the saved draft. */
export function draftState(format: DisclosureFormat, draft: DisclosureDraft): DraftState {
  const state: DraftState = { values: {}, results: {}, documents: {} };
  for (const section of format.sections) {
    for (const item of section.items) {
      const saved = draft.inputs[item.key];
      if (item.type === "results") state.results[item.key] = asRows(saved);
      else if (item.fill === "input" || item.fill === "link") {
        state.values[item.key] = asText(saved);
      }
      if (item.takes_documents) {
        state.documents[item.key] = (draft.documents[item.key] ?? []).map((d) => d.id);
      }
    }
  }
  return state;
}

function wholeNumber(text: string): number | string {
  const trimmed = text.trim();
  return /^\d{1,9}$/.test(trimmed) ? Number(trimmed) : trimmed;
}

/**
 * The PUT body. Numbers go as numbers when they look like whole numbers (anything else is sent
 * as typed, so the API answers with the item's own error); yes/no as booleans; empty values are
 * left out (the API clears them).
 */
export function draftBody(
  format: DisclosureFormat,
  state: DraftState,
): { inputs: Record<string, unknown>; documents: Record<string, string[]> } {
  const inputs: Record<string, unknown> = {};
  const documents: Record<string, string[]> = {};
  for (const section of format.sections) {
    for (const item of section.items) {
      if (item.type === "results") {
        const rows = (state.results[item.key] ?? []).filter(
          (r) => r.year.trim() || r.appeared.trim() || r.passed.trim(),
        );
        if (rows.length) {
          inputs[item.key] = rows.map((r) => ({
            year: r.year.trim(),
            class_level: r.class_level,
            appeared: wholeNumber(r.appeared),
            passed: wholeNumber(r.passed),
          }));
        }
      } else if (item.fill === "input" || item.fill === "link") {
        const text = (state.values[item.key] ?? "").trim();
        if (!text) continue;
        if (item.type === "yes_no") inputs[item.key] = text === "yes";
        else if (item.type === "integer") inputs[item.key] = wholeNumber(text);
        else inputs[item.key] = text;
      }
      const chosen = state.documents[item.key] ?? [];
      if (item.takes_documents && chosen.length) documents[item.key] = chosen;
    }
  }
  return { inputs, documents };
}

/** `inputs.affiliation_no` → the item key; `documents.fire_safety` → the item key. */
export function itemOfField(field: string): string | null {
  const match = /^(?:inputs|documents)\.([a-z][a-z0-9_]*)$/.exec(field);
  return match ? (match[1] ?? null) : null;
}

/** The live snapshot, if any (the page shows it now). */
export function liveSnapshot(snapshots: readonly Snapshot[]): Snapshot | null {
  return snapshots.find((s) => s.live) ?? null;
}

/**
 * Open a short-lived download link at once (never kept in state, storage or logs). Tests replace
 * the opener (jsdom cannot navigate).
 */
let openDownload: (url: string) => void = (url) => window.location.assign(url);
export function setDisclosureDownloadOpenerForTesting(
  opener: ((url: string) => void) | null,
): void {
  openDownload = opener ?? ((url) => window.location.assign(url));
}
export function startDownload(url: string): void {
  openDownload(url);
}
