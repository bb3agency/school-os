import { PROFILE_KEY_PATTERN, type RequestedBy } from "./types";

/**
 * URL parameters of the export screens (plain module: server pages call these). Only codes and
 * keys, never personal details; unknown values are dropped instead of being sent to the API.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | null {
  const head = Array.isArray(value) ? value[0] : value;
  const trimmed = head?.trim();
  return trimmed ? trimmed : null;
}

export interface ExportListFilters {
  /** `me` (default): your own exports. `all`: every export of the school (export.read_all). */
  view: RequestedBy;
}

export function parseExportListFilters(params: SearchParams): ExportListFilters {
  return { view: first(params.view) === "all" ? "all" : "me" };
}

export interface NewPrecheckParams {
  /** Preselected profile (e.g. "make a new one" from an expired export). */
  profileKey: string | null;
}

export function parseNewPrecheckParams(params: SearchParams): NewPrecheckParams {
  const profile = first(params.profile);
  return { profileKey: profile && PROFILE_KEY_PATTERN.test(profile) ? profile : null };
}
