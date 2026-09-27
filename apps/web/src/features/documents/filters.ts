import {
  DOC_STATUSES,
  DOC_TYPES,
  PURPOSES,
  type DocStatus,
  type DocType,
  type Purpose,
} from "./types";

/**
 * URL parameters of the documents list (plain module: server pages call it). Only codes, never
 * titles or personal details; unknown values are dropped instead of being sent to the API.
 */
export type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | null {
  const head = Array.isArray(value) ? value[0] : value;
  const trimmed = head?.trim();
  return trimmed ? trimmed : null;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

export interface DocumentListFilters {
  purpose: Purpose | null;
  docType: DocType | null;
  status: DocStatus | null;
}

export function parseDocumentListFilters(params: SearchParams): DocumentListFilters {
  return {
    purpose: oneOf(first(params.purpose), PURPOSES),
    docType: oneOf(first(params.doc_type), DOC_TYPES),
    status: oneOf(first(params.status), DOC_STATUSES),
  };
}

/** A stable key so the screen restarts paging when the filters change. */
export function filtersKey(filters: DocumentListFilters): string {
  return [filters.purpose ?? "", filters.docType ?? "", filters.status ?? ""].join("|");
}

/** `?deleted=1` after a delete: the list says so once (no id or title in the URL). */
export function parseDeletedNotice(params: SearchParams): boolean {
  return first(params.deleted) === "1";
}
